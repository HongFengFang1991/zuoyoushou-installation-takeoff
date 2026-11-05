"""Cross-host local pipeline. No LLM/API calls. Python 3.11+."""
import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile

from prepare_quote import prepare, write_outputs, VERSION

SKILL = Path(__file__).resolve().parent.parent
VERSION_SKILL = '1.3.0'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def atomic_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@contextmanager
def stage(root, name):
    start = time.monotonic()
    event = dict(stage=name, started_at=now(), status='ok', model_tokens=None, platform_credits=None)
    try:
        yield event
    except Exception as exc:
        event.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        event['seconds'] = round(time.monotonic() - start, 3)
        try:
            with (Path(root) / 'run-log.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except OSError:
            pass  # A write-denied error must remain visible even when logging also fails.


def state(root):
    s = read_json(Path(root) / 'state.json')
    if s.get('runtime_version') != VERSION_SKILL:
        raise ValueError('工作目录版本不兼容；v1.2请运行 migrate --from 原目录 到新目录，保留原目录，不重新识图')
    return s


def safe_sources(row):
    value=row.get('sources')
    return [v for v in value if isinstance(v,dict) and isinstance(v.get('file'),str) and type(v.get('page')) is int] if isinstance(value,list) else []


def load_takeoff(root):
    return copy.deepcopy(state(root)['takeoff'])


def document_path(root, doc):
    if doc.get('stored_path'):
        path = (Path(root) / doc['stored_path']).resolve()
        if not path.is_relative_to(Path(root).resolve()):
            raise ValueError('源快照路径越界')
        return path
    return Path(doc['path'])


def verify_sources(s, root):
    for d in s['documents']:
        p = document_path(root, d)
        if not p.is_file() or digest(p) != d['sha256']:
            raise ValueError(f"源文件已变化或丢失：{d['file']}；请建立新的修订项目，不能复用旧图缓存")


def render_source(doc, page_number, edge):
    from PIL import Image, ImageOps
    p = Path(doc['path'])
    if p.suffix.lower() == '.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(str(p)) as pdf:
            page = pdf[page_number - 1]
            scale = edge / max(page.get_size())
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert('RGB').copy()
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close(); bitmap.close(); page.close()
            return image, text
    with Image.open(p) as im:
        im.seek(page_number - 1)
        image = ImageOps.exif_transpose(im).convert('RGB').copy()
    image.thumbnail((edge, edge))
    return image, ''


def initialize(root, sources, project, model='未记录', evidence_mode='visual'):
    root = Path(root).resolve()
    if (root / 'state.json').exists():
        raise ValueError('该目录已有项目，请使用status后继续，不要重新init')
    root.mkdir(parents=True, exist_ok=True)
    with stage(root, 'preflight') as event:
        versions = {n: importlib.metadata.version(n) for n in ('pypdfium2', 'Pillow', 'XlsxWriter')}
        import pypdfium2 as pdfium
        from PIL import Image
        import xlsxwriter
        docs = []
        names = set()
        for i, source in enumerate(sources, 1):
            p = Path(source).expanduser().resolve()
            if not p.is_file() or p.name in names:
                raise ValueError('源文件不存在或重名，请提供唯一文件名')
            names.add(p.name)
            if p.suffix.lower() == '.pdf':
                with pdfium.PdfDocument(str(p)) as pdf:
                    count = len(pdf)
            elif p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp', '.bmp'}:
                with Image.open(p) as im:
                    count = getattr(im, 'n_frames', 1)
            else:
                raise ValueError('此入口读取PDF/图片；DWG/DXF先本地转PDF并核对布局、字体及单位')
            if count < 1:
                raise ValueError('文件没有页面')
            docs.append(dict(id=f'D{i:03}', file=p.name, path=str(p), page_count=count, sha256=digest(p)))
        if not docs:
            raise ValueError('需要至少一个源文件')
        # Probe actual read/render/write/rename/delete and embedded-image XLSX before vision work.
        with tempfile.TemporaryDirectory(prefix='.preflight-', dir=root) as tmp:
            tmp = Path(tmp)
            probe, _ = render_source(docs[0], 1, 500)
            probe.save(tmp / 'probe.png')
            book = tmp / 'probe.xlsx'
            with xlsxwriter.Workbook(str(book)) as wb:
                ws = wb.add_worksheet('检查')
                ws.write(0, 0, '预检'); ws.write_formula(0, 1, '=1+1', None, 2)
                ws.insert_image(1, 0, str(tmp / 'probe.png'))
            moved = tmp / 'reopen.xlsx'; os.replace(book, moved)
            with zipfile.ZipFile(moved) as z:
                if z.testzip() or not any(n.startswith('xl/media/') for n in z.namelist()):
                    raise ValueError('嵌图工作簿预检失败')
            moved.unlink()
        for d in docs:
            snapshot = root / 'sources' / (d['id'] + '-' + d['file'])
            snapshot.parent.mkdir(exist_ok=True)
            shutil.copy2(d['path'], snapshot)
            if digest(snapshot) != d['sha256']:
                raise ValueError('建立快照时源文件发生变化，请重试新项目')
            d['stored_path'] = str(snapshot.relative_to(root))
        project_id = str(uuid.uuid4())
        source_fingerprint = fingerprint([(d['file'],d['sha256'],d['page_count']) for d in docs])
        takeoff = dict(schema_version=1, evidence_policy=3, project=project, rule_version=VERSION,
                       project_id=project_id, source_fingerprint=source_fingerprint, revision=0,
                       pending_review_inputs=[],
                       evidence_mode=evidence_mode, documents=[dict(file=d['file'], page_count=d['page_count']) for d in docs],
                       pages=[], items=[], rule_overrides=[], changes=[],
                       model_info=dict(model=model, started_at=now(), client='用户启动的本地智能体'))
        s = dict(runtime_version=VERSION_SKILL, created_at=now(), documents=docs,
                 rendered={}, crops={}, batches={}, revision=0, takeoff=takeoff)
        report = dict(ok=True, checked_at=now(), python=sys.version.split()[0], packages=versions,
                      read_render_write_rename_delete_xlsx=True, source_pages=sum(d['page_count'] for d in docs),
                      note='只检验本地工具；模型能否查看图像仍需宿主实际打开一页验证')
        atomic_json(root / 'preflight.json', report)
        atomic_json(root / 'state.json', s)
        event['source_pages'] = report['source_pages']
        return s


def render_pages(root, only=None):
    root = Path(root); s = state(root); verify_sources(s, root)
    rows = []
    with stage(root, 'render') as event:
        made = reused = 0
        for d in s['documents']:
            for p in range(1, d['page_count'] + 1):
                key = f"{d['id']}:{p}"
                if only and key not in only:
                    continue
                record = s['rendered'].get(key)
                if record and all(k in record and (root / record[k]).is_file() and digest(root / record[k]) == record[k + '_sha256'] for k in ('image', 'text', 'index')):
                    reused += 1; rows.append(record); continue
                directory = root / 'pages' / d['id']; directory.mkdir(parents=True, exist_ok=True)
                ip = directory / f'{p:03}.png'; tp = directory / f'{p:03}.txt'
                if not (record and all(k in record and (root/record[k]).is_file() and digest(root/record[k])==record.get(k+'_sha256') for k in ('image','text'))):
                    im, text = render_source(dict(d,path=str(document_path(root,d))),p,1800)
                    im.save(ip); tp.write_text(text,encoding='utf-8')
                else:
                    # Migration/new index does not rasterize an unchanged source again.
                    ip=root/record['image']; tp=root/record['text']
                from source_evidence import index_document_page
                index = index_document_page(document_path(root,d), p)
                index['file'] = d['file']
                jp = directory / f'{p:03}.index.json'; atomic_json(jp,index)
                record = dict(file=d['file'], doc_id=d['id'], page=p, image=str(ip.relative_to(root)),
                              text=str(tp.relative_to(root)), index=str(jp.relative_to(root)), image_sha256=digest(ip), text_sha256=digest(tp), index_sha256=digest(jp))
                s['rendered'][key] = record
                atomic_json(root / 'state.json', s)
                made += 1; rows.append(record)
        event.update(rendered_pages=made, reused_pages=reused)
        atomic_json(root / 'page-index.json', list(s['rendered'].values()))
    return rows


def crop(root, row_id, doc_id, page, box, edge=4200):
    root = Path(root); s = state(root); verify_sources(s, root)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row_id):
        raise ValueError('行ID只用英文字母、数字、下划线或短横线')
    if len(box) != 4 or not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1:
        raise ValueError('裁图box须是左上原点的归一化x0,y0,x1,y1')
    if edge < 1800 or edge > 8000:
        raise ValueError('局部图整页渲染长边须在1800至8000像素内')
    d = next((d for d in s['documents'] if d['id'] == doc_id), None)
    if not d or not 1 <= page <= d['page_count']:
        raise ValueError('裁图页不存在')
    request = dict(row_id=row_id, file=d['file'], doc_id=doc_id, page=page, box=box, edge=edge, source_sha256=d['sha256'])
    token = fingerprint(request)[:16]
    dest = root / 'thumbnails' / f'{row_id}-{token}.png'
    with stage(root, 'crop') as event:
        old = s['crops'].get(row_id)
        if old and old.get('token') == token and dest.is_file() and digest(dest) == old['sha256']:
            event['cache_hit'] = True; return old
        im, _ = render_source(dict(d,path=str(document_path(root,d))), page, edge)
        w, h = im.size
        im = im.crop((int(box[0]*w), int(box[1]*h), int(box[2]*w), int(box[3]*h)))
        im.thumbnail((1800, 1800))
        dest.parent.mkdir(parents=True, exist_ok=True); im.save(dest)
        result = dict(request, image=str(dest.relative_to(root)), sha256=digest(dest), token=token)
        s['crops'][row_id] = result; atomic_json(root / 'state.json', s)
        return result


def checkpoint(root, batch):
    root = Path(root); s = state(root); verify_sources(s, root)
    bid = batch.get('batch_id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', bid):
        raise ValueError('batch_id须为唯一英文/数字/短横线编号')
    if set(batch) - {'batch_id', 'pages', 'items', 'delete_ids', 'settings', 'reason', 'model_seconds', 'platform_credits', 'resolved_inputs'}:
        raise ValueError('批次包含未知字段')
    sha = fingerprint(batch)
    if bid in s['batches']:
        if sha == s['batches'][bid]:
            return dict(reused=True, revision=s['revision'])
        raise ValueError('相同批次ID内容变化，请使用新的批次ID保留历史')
    with stage(root, 'checkpoint') as event:
        before = s['takeoff']; new = copy.deepcopy(before)
        pages = {(p['file'], p['page']): p for p in new['pages']}
        for p in batch.get('pages', []):
            d = next((d for d in s['documents'] if d['file'] == p.get('file')), None)
            if not d or f"{d['id']}:{p.get('page')}" not in s['rendered']:
                raise ValueError('页面尚未渲染，不能登记已阅')
            if not p.get('reason'):
                raise ValueError('页面分类须填写判断依据reason')
            pages[(p['file'], p['page'])] = p
        new['pages'] = list(pages.values())
        rows = {r['id']: r for r in new['items']}
        incoming = batch.get('items', [])
        if len({r.get('id') for r in incoming}) != len(incoming):
            raise ValueError('批次内行ID重复')
        deleted = set(batch.get('delete_ids', []))
        if deleted & {r.get('id') for r in incoming} or deleted - set(rows):
            raise ValueError('删除ID不存在或同时被修改')
        if (deleted or any(r.get('id') in rows for r in incoming) or batch.get('settings')) and not batch.get('reason'):
            raise ValueError('修改已有结果/规则需reason，保留修改原因')
        for row_id in deleted:
            del rows[row_id]
        for row in incoming:
            if not isinstance(row.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row['id']):
                raise ValueError('明细ID须为英文/数字/短横线/下划线')
            rows[row['id']] = copy.deepcopy(dict(rows.get(row['id'], {}), **row))
        # Source cross-check is deterministic and runs only for changed observations.
        needed_sources = {(v.get('file'),v.get('page')) for r in incoming if set(r) & {'sources','evidence','length_mm','height_mm','depth_mm','installation_lengths_mm','components'} for v in safe_sources(rows[r['id']])}
        indexes = load_indexes(root, s, needed_sources)
        from source_evidence import verify_readings
        for incoming_row in incoming:
            if not (set(incoming_row) & {'sources','evidence','length_mm','height_mm','depth_mm','installation_lengths_mm','components'}):
                continue
            row = rows[incoming_row['id']]
            if isinstance(row.get('evidence'),dict):
                view = copy.deepcopy(row)
                for source in view.get('sources',[]) if isinstance(view.get('sources'),list) else []:
                    if isinstance(source,dict): source.setdefault('role','measurement')
                checks = verify_readings(view,indexes)
                row['evidence']['source_checks'] = checks['checks']
        new['items'] = list(rows.values())
        for resolution in batch.get('resolved_inputs',[]):
            if not isinstance(resolution,dict) or not resolution.get('reason'):
                raise ValueError('解决反馈需要id和具体reason')
            entry = next((v for v in new.get('pending_review_inputs',[]) if v['id']==resolution.get('id')),None)
            if not entry or entry.get('status')!='pending':
                raise ValueError('待处理反馈不存在或已处理')
            if entry['row_id'] not in {r['id'] for r in incoming}:
                raise ValueError('处理反馈须同时提交该行的字段修订/证据，不能仅清除标记')
            patch = next(r for r in incoming if r['id']==entry['row_id'])
            original = next((r for r in before['items'] if r['id']==entry['row_id']),{})
            meaningful = {'evidence','sources','length_mm','height_mm','depth_mm','installation_lengths_mm','components','count','user_confirmation','manual_quantity','manual_basis','product_type','cabinet_subtype'}
            if not any(key in patch and patch[key]!=original.get(key) for key in meaningful):
                raise ValueError('解决反馈必须实际修订相关字段/证据或新增用户确认原话；只有ID或解除标记不足')
            entry.update(status='resolved',resolution=resolution['reason'],resolved_at=now())
        settings = batch.get('settings', {})
        if set(settings) - {'rule_overrides', 'model_info'}:
            raise ValueError('settings仅支持rule_overrides和model_info；不能降低证据检查策略')
        new.update(settings)
        new['changes'].append(dict(batch_id=bid, reason=batch.get('reason', '新批次识图'), at=now()))
        result = prepare(new)  # Invalid global schema fails before saving; doubtful rows remain review.
        old_result = prepare(before)
        old_rows = {r['id']: r for r in old_result['rows'] + old_result['review']}
        diff = []
        for r in result['rows'] + result['review']:
            old = old_rows.pop(r['id'], {})
            change = {k: dict(before=old.get(k), after=r.get(k)) for k in ('quantity', 'unit', 'unit_price', 'amount', 'status', 'reason') if old.get(k) != r.get(k)}
            if change:
                diff.append(dict(id=r['id'], changes=change))
        diff += [dict(id=k, deleted=True) for k in old_rows]
        record = dict(batch=batch, before=before, after=new, diff=diff)
        atomic_json(root / 'changes' / f'{bid}.json', record)
        s['revision'] += 1; new['revision'] = s['revision']; s['takeoff'] = new; s['batches'][bid] = sha
        atomic_json(root / 'state.json', s)
        event.update(batch_id=bid, rows=len(incoming), declared_model_seconds=batch.get('model_seconds'),
                     platform_credits=batch.get('platform_credits'))
        return dict(revision=s['revision'], confirmed=len(result['rows']), review=len(result['review']), diff=diff)


def load_indexes(root, s, only=None):
    indexes = {}
    for record in s['rendered'].values():
        if only is not None and (record['file'],record['page']) not in only: continue
        if not record.get('index'): continue
        path = Path(root) / record['index']
        if not path.is_file() or digest(path)!=record['index_sha256']:
            raise ValueError('文字坐标索引缓存损坏；仅重新render对应页面')
        indexes[(record['file'],record['page'])] = read_json(path)
    return indexes


def status(root, full=False):
    root = Path(root); s = state(root); verify_sources(s, root)
    seen = {(p['file'],p['page']) for p in s['takeoff']['pages'] if p['page_type']!='unreadable'}
    remaining = [dict(doc_id=d['id'],file=d['file'],page=p,image=f"pages/{d['id']}/{p:03}.png")
                 for d in s['documents'] for p in range(1,d['page_count']+1) if (d['file'],p) not in seen]
    result = prepare(s['takeoff'])
    out = dict(version=VERSION_SKILL,revision=s['revision'],project=s['takeoff']['project'],
               total_pages=sum(d['page_count'] for d in s['documents']),remaining_pages=len(remaining),
               next_pages=remaining[:4],confirmed=len(result['rows']),review=len(result['review']),
               summary=result['quote_summary'],verified_summary=result['summary'],evidence_policy=s['takeoff']['evidence_policy'],
               pending_inputs=sum(v.get('status')=='pending' for v in s['takeoff'].get('pending_review_inputs',[])),
               coverage={k:result[k] for k in ('page_coverage_complete','product_coverage_complete','accessory_coverage_complete')},
               coverage_issues=result['coverage_issues'][:12], complete_total=result['complete_total'])
    if full:
        out['entity_index'] = [dict(id=r['id'],name=r.get('name'),space=r.get('space'),parent_id=r.get('parent_id'),
               sources=[dict(file=v.get('file'),page=v.get('page')) for v in safe_sources(r)]) for r in s['takeoff']['items']]
    return out


def next_batch(root, limit=4):
    if not 1<=limit<=6: raise ValueError('每批1至6页，默认4页')
    root=Path(root); s=state(root); info=status(root)
    pages=info['next_pages'][:limit]
    # More than four requested remains bounded by the default packet.
    for page in pages:
        rec=s['rendered'].get(f"{page['doc_id']}:{page['page']}")
        if rec: page.update(image=rec['image'],text=rec['text'],index=rec.get('index'),needs_index=not bool(rec.get('index')))
        page['rendered']=bool(rec)
    nearby={(p['file'],p['page']+d) for p in pages for d in (-1,0,1)}
    related=[dict(id=r['id'],name=r.get('name'),space=r.get('space'),parent_id=r.get('parent_id')) for r in s['takeoff']['items']
             if any((v.get('file'),v.get('page')) in nearby for v in safe_sources(r))]
    return dict(revision=s['revision'],pages=pages,related_entities=related[:16],related_truncated=len(related)>16,
                remaining_pages=info['remaining_pages'],pending_inputs=info['pending_inputs'],
                next='逐页看图、按视图登记并checkpoint；无未读页时audit检查疑点。跨页去重必要时status --full。')


def query_page(root, doc_id, page, query='', limit=30, region=None):
    from source_evidence import query_index
    root=Path(root); s=state(root); verify_sources(s,root)
    rec=s['rendered'].get(f'{doc_id}:{page}')
    if not rec: raise ValueError('请先render对应页面')
    idx=load_indexes(root,s,{(rec['file'],page)}).get((rec['file'],page))
    if idx is None: raise ValueError('此旧缓存缺少数字索引，仅render指定页面补索引即可')
    return query_index(idx,query=query,limit=limit,region=region)


def product_thumbnail(root, s, row):
    """Resolve only a registered crop of this item's measurement page.

    Crop IDs are tool handles, not necessarily item IDs. Never fall back to the
    first rendered source: a source may be a room plan used only for context.
    """
    root = Path(root).resolve()
    sources = {(v.get('file'), v.get('page')) for v in safe_sources(row)
               if v.get('role', 'measurement') == 'measurement'}
    products = {(p.get('file'), p.get('page')) for p in s['takeoff'].get('pages', [])
                if p.get('page_type') == 'product_drawing'}
    docs = {d['file']: d['sha256'] for d in s['documents']}
    candidates = []
    requested = row.get('thumbnail')
    if isinstance(requested, str) and requested:
        requested_path = (root / requested).resolve()
        if requested_path.is_relative_to(root):
            candidates.extend(r for r in s.get('crops', {}).values()
                              if (root / r['image']).resolve() == requested_path)
    bound = s.get('crops', {}).get(row['id'])
    if bound:
        candidates.append(bound)
    for record in candidates:
        key = (record.get('file'), record.get('page'))
        if key not in sources or key not in products:
            continue
        if record.get('source_sha256') != docs.get(record.get('file')):
            continue
        path = (root / record['image']).resolve()
        if not path.is_relative_to(root):
            continue
        if not path.is_file() or digest(path) != record.get('sha256'):
            raise ValueError('缩略图缓存损坏；仅补对应裁图')
        return str(path)
    return None


def result_for(root,s):
    data=copy.deepcopy(s['takeoff'])
    thumbnail_issues=[]
    for row in data['items']:
        thumbnail=product_thumbnail(root,s,row)
        row.pop('thumbnail',None)
        if thumbnail:
            row['thumbnail']=thumbnail
        elif not row.get('excluded_reason'):
            thumbnail_issues.append(dict(row_id=row['id'],reason='待补产品局部图：只接受本项计量页登记的裁图，不使用户型图或整页替代'))
    result=prepare(data)
    result.update(project_id=data['project_id'],source_fingerprint=data['source_fingerprint'],revision=s['revision'],
                  all_items=data['items'],pending_review_inputs=data.get('pending_review_inputs',[]),
                  thumbnail_issues=thumbnail_issues)
    return data,result


def commit_aux(root,s,new,reason,bid):
    before=copy.deepcopy(s['takeoff'])
    s['revision']+=1; new['revision']=s['revision']
    new.setdefault('changes',[]).append(dict(batch_id=bid,reason=reason,at=now()))
    atomic_json(Path(root)/'changes'/f'{bid}.json',dict(before=before,after=new,reason=reason))
    s['takeoff']=new; atomic_json(Path(root)/'state.json',s)


def import_review(root,payload):
    root=Path(root); s=state(root)
    if payload.get('kind')!='review_inputs' or payload.get('schema_version')!=1:
        raise ValueError('需要review_inputs schema_version=1反馈文件')
    if payload.get('project_id')!=s['takeoff']['project_id']:
        raise ValueError('复核反馈项目UUID不匹配')
    token=fingerprint(payload)
    if token in s.get('review_imports',[]): return dict(reused=True,revision=s['revision'])
    if payload.get('base_revision')!=s['revision']:
        raise ValueError('复核卡片修订号已变化，请重新生成卡片并对照反馈后再导入；不自动覆盖新结果')
    rows={r['id'] for r in s['takeoff']['items']}
    inputs=payload.get('inputs')
    if not isinstance(inputs,list) or not inputs: raise ValueError('反馈不能为空')
    new=copy.deepcopy(s['takeoff'])
    for value in inputs:
        if not isinstance(value,dict) or value.get('row_id') not in rows or not isinstance(value.get('text'),str) or not value['text'].strip():
            raise ValueError('反馈需要有效row_id和非空text')
        new.setdefault('pending_review_inputs',[]).append(dict(id=uuid.uuid4().hex,row_id=value['row_id'],
             text=value['text'],source='review_cards',baseline_revision=s['revision'],status='pending'))
    s.setdefault('review_imports',[]).append(token)
    commit_aux(root,s,new,'保存人工反馈，尚未作为确认尺寸','review-'+token[:16])
    return dict(reused=False,revision=s['revision'],imported_pending=len(inputs),total_pending=sum(v.get('status')=='pending' for v in new['pending_review_inputs']),next='按反馈逐字段修改，checkpoint resolved_inputs记录处理依据')


def export_project(root, previous=None):
    from export_quote import export_workbook,reconcile_previous
    root=Path(root); s=state(root); verify_sources(s,root)
    with stage(root,'export') as event:
        data,result=result_for(root,s)
        info=reconcile_previous(Path(previous),result) if previous else None
        if info:
            new=copy.deepcopy(s['takeoff']); patches=info.get('item_patches',{})
            for row in new['items']: row.update(patches.get(row['id'],{}))
            old_pending=new.get('pending_review_inputs',[])
            pending=copy.deepcopy(info.get('pending_review_inputs',[]))
            for entry in pending: entry.setdefault('id',fingerprint(entry)[:24])
            # Reconcile returns baseline/current pending plus genuine new H text.
            by_id={entry.get('id') or fingerprint(entry)[:24]:entry for entry in old_pending}
            for entry in pending: by_id.setdefault(entry['id'],entry)
            new['pending_review_inputs']=list(by_id.values())
            if new!=s['takeoff']:
                commit_aux(root,s,new,'接续人工Excel价格/备注/待处理确认','excel-'+uuid.uuid4().hex[:16])
                data,result=result_for(root,s)
        name=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
        parent=root/'exports'; parent.mkdir(exist_ok=True)
        partial=parent/('.pending-'+name); partial.mkdir()
        dest=partial/'安装报价表.xlsx'
        meta=export_workbook(result,dest,info)
        write_outputs(result,partial/'calculation')
        atomic_json(partial/'takeoff.json',data)
        if info: atomic_json(partial/'manual-edits.json',info)
        atomic_json(dest.with_suffix('.manifest.json'),meta)
        from review_cards import generate_review_cards
        generate_review_cards(result,partial/'逐项复核.html')
        with zipfile.ZipFile(dest) as z:
            if z.testzip(): raise ValueError('导出XLSX校验失败')
        final=parent/name; os.replace(partial,final)
        event.update(rows=len(result['rows']),review=len(result['review']),workbook=str(final/dest.name))
        return final/dest.name


def audit_project(root):
    s=state(root); verify_sources(s,root); _,result=result_for(root,s)
    report=status(root)
    report['review_items']=[dict(id=r['id'],reason=r['reason']) for r in result['review']]
    report['missing_prices']=[r['id'] for r in result['quote_rows'] if r['unit_price'] is None]
    report['quotation_summary']=result['quote_summary']
    report['quotation_items']=len(result['quote_rows'])
    report['quotation_unmeasured']=result['quote_unmeasured_count']
    report['thumbnail_issues']=result.get('thumbnail_issues',[])
    report['quotation_note']='报价数量含待复核暂计量；verified_summary为依据齐全量，不能将暂计量混称最终确认量'
    report['pending_review_inputs']=[v for v in s['takeoff'].get('pending_review_inputs',[]) if v.get('status')=='pending']
    report['source_conflicts']=[dict(id=r['id'],check=c) for r in s['takeoff']['items'] for c in ((r.get('evidence') or {}).get('source_checks',[]) if isinstance(r.get('evidence'),dict) else []) if isinstance(c,dict) and c.get('status')=='conflict']
    report['note']='本地核验不能独立证明模型看图正确。最终仍需用户逐项核对边界、尺寸、漏项。'
    atomic_json(Path(root)/'audit.json',report)
    return report


def profile_action(root,path,action,out=None):
    from provider_profiles import preview_profile,apply_profile,save_profile
    profile=read_json(path)
    if action=='save':
        if out is None: raise ValueError('保存档案需--out明确指定长期存放位置')
        return dict(saved=str(save_profile(out,profile)))
    root=Path(root); s=state(root)
    preview=preview_profile(s['takeoff'],profile)
    if action=='preview': return preview
    new,changes=apply_profile(s['takeoff'],profile)
    # Native checks already cover saved readings; re-evaluate only local quantities.
    prepare(new)
    if new==s['takeoff']: return dict(reused=True,revision=s['revision'],changes=[],preview=preview)
    commit_aux(root,s,new,'应用用户选择的服务商档案：'+profile['source_text'],'profile-'+uuid.uuid4().hex[:16])
    return dict(revision=s['revision'],changes=changes,preview=preview)


def main():
    p=argparse.ArgumentParser(description='本地算量v1.3：缓存/证据/断点/复核/带图Excel，无模型API调用')
    p.add_argument('--project-dir',required=True,type=Path)
    sub=p.add_subparsers(dest='command',required=True)
    i=sub.add_parser('init'); i.add_argument('--source',action='append',required=True); i.add_argument('--name',required=True)
    i.add_argument('--model',default='未记录'); i.add_argument('--evidence-mode',choices=['visual','synthetic','text_observations'],default='visual')
    r=sub.add_parser('render'); r.add_argument('--pages')
    st=sub.add_parser('status'); st.add_argument('--full',action='store_true')
    n=sub.add_parser('next'); n.add_argument('--limit',type=int,default=4)
    q=sub.add_parser('query'); q.add_argument('--doc',required=True); q.add_argument('--page',type=int,required=True)
    q.add_argument('--query',default=''); q.add_argument('--limit',type=int,default=30); q.add_argument('--region')
    for command in ('checkpoint','import-review'):
        c=sub.add_parser(command); c.add_argument('--input',type=Path,required=True)
    c=sub.add_parser('crop'); c.add_argument('--id',required=True); c.add_argument('--doc',required=True)
    c.add_argument('--page',type=int,required=True); c.add_argument('--box',required=True); c.add_argument('--edge',type=int,default=4200)
    e=sub.add_parser('export'); e.add_argument('--previous',type=Path)
    sub.add_parser('audit'); sub.add_parser('doctor'); sub.add_parser('upgrade-evidence')
    rv=sub.add_parser('review'); rv.add_argument('--out',type=Path)
    ar=sub.add_parser('archive'); ar.add_argument('--out',type=Path,required=True)
    rs=sub.add_parser('restore'); rs.add_argument('--input',type=Path,required=True)
    mg=sub.add_parser('migrate'); mg.add_argument('--from',dest='old_root',type=Path,required=True)
    pr=sub.add_parser('profile'); pr.add_argument('action',choices=['preview','apply','save']); pr.add_argument('--input',type=Path,required=True); pr.add_argument('--out',type=Path)
    args=p.parse_args(); root=args.project_dir.expanduser().resolve(); lock=root/'.runtime.lock'; acquired=False
    try:
        # Archive/migration helpers stage fresh destinations and do not take a target lock.
        if args.command=='restore':
            from project_tools import restore_archive
            s=restore_archive(args.input,root); out=dict(project_dir=str(root),version=s['runtime_version'])
        elif args.command=='migrate':
            from project_tools import migrate_project
            s=migrate_project(args.old_root,root); out=dict(project_dir=str(root),version=s['runtime_version'],next='status；旧证据仅草稿，补充v3证据后再审核')
        else:
            if args.command=='init': root.mkdir(parents=True,exist_ok=True)
            elif not (root/'state.json').is_file(): raise ValueError('项目不存在，请先init或指定正确目录')
            fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY); acquired=True
            with os.fdopen(fd,'w') as f: f.write(json.dumps(dict(pid=os.getpid(),started=now())))
            if args.command=='init':
                initialize(root,args.source,args.name,args.model,args.evidence_mode)
                out=dict(preflight='passed',project_dir=str(root),next='render')
            elif args.command=='render':
                rows=render_pages(root,set(args.pages.split(',')) if args.pages else None)
                out=dict(available_pages=len(rows),page_index=str(root/'page-index.json'),next='next')
            elif args.command=='status': out=status(root,args.full)
            elif args.command=='next': out=next_batch(root,args.limit)
            elif args.command=='query': out=query_page(root,args.doc,args.page,args.query,args.limit,list(map(float,args.region.split(','))) if args.region else None)
            elif args.command=='checkpoint': out=checkpoint(root,read_json(args.input))
            elif args.command=='import-review': out=import_review(root,read_json(args.input))
            elif args.command=='crop': out=crop(root,args.id,args.doc,args.page,list(map(float,args.box.split(','))),args.edge)
            elif args.command=='export': out=dict(workbook=str(export_project(root,args.previous)))
            elif args.command=='audit': out=audit_project(root)
            elif args.command=='upgrade-evidence':
                s=state(root); new=copy.deepcopy(s['takeoff'])
                if new.get('evidence_policy')!=3:
                    new['evidence_policy']=3
                    commit_aux(root,s,new,'启用v3检查，原始观察不改，缺失证据须补充','evidence-'+uuid.uuid4().hex[:16])
                out=audit_project(root)
            elif args.command=='review':
                from review_cards import generate_review_cards
                s=state(root); verify_sources(s,root); _,result=result_for(root,s)
                out=dict(review=str(generate_review_cards(result,args.out or root/'逐项复核.html')))
            elif args.command=='archive':
                from project_tools import archive_project
                out=dict(archive=str(archive_project(root,args.out)))
            elif args.command=='profile': out=profile_action(root,args.input,args.action,args.out)
            else:
                out=dict(runtime_version=VERSION_SKILL,python=sys.version.split()[0],packages={n:importlib.metadata.version(n) for n in ('pypdfium2','Pillow','XlsxWriter')},preflight=read_json(root/'preflight.json') if (root/'preflight.json').exists() else None,
                         capabilities=['PDF/图片本地渲染','PDF原生文字坐标','规则算量','XLSX/HTML导出'],model_vision='需宿主实际打开一页验证；本脚本不能代验',ocr='未内置',cad='需本地转换并核对')
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False,default=str))
    except Exception as exc:
        p.exit(2,f'失败 [{type(exc).__name__}]：{exc}\n已有state和批次保留。定位失败阶段后最多修复重试一次，不重跑整套识图。\n')
    finally:
        if acquired: lock.unlink(missing_ok=True)


if __name__=='__main__': main()
