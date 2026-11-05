"""Self-contained local review HTML. Feedback downloads never change calculated data."""
import base64
from html import escape
from io import BytesIO
import json
from pathlib import Path


def _text(value):
    if value is None: return '—'
    if isinstance(value,(dict,list)): return json.dumps(value,ensure_ascii=False,indent=2)
    return str(value)


def _image(path, base):
    if not path or not isinstance(path,(str,Path)) or str(path).startswith(('http:','https:','data:')): return ''
    from PIL import Image, ImageOps
    file=Path(path).expanduser()
    if not file.is_absolute(): file=base/file
    try:
        with Image.open(file) as image:
            image=ImageOps.exif_transpose(image).convert('RGB'); image.thumbnail((2000,2000))
            stream=BytesIO(); image.save(stream,format='PNG')
        return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii')
    except (OSError,ValueError): return ''


def generate_review_cards(result, output_path):
    """Write one offline HTML; result must include project_id and revision.

    Optional _base_dir resolves local thumbnail paths. A card shows immutable
    observations; free-text inputs export {kind:'review_inputs',project_id,
    base_revision,inputs:[{row_id,text}]}. Only a separate reviewed checkpoint may
    apply them. No server, browser automation, storage, or external resource.
    """
    project_id=result.get('project_id'); revision=result.get('revision',result.get('base_revision'))
    if not isinstance(project_id,str) or not project_id or not isinstance(revision,int) or isinstance(revision,bool) or revision<0:
        raise ValueError('复核卡片必须绑定project_id和非负整数revision')
    dest=Path(output_path).resolve(); dest.parent.mkdir(parents=True,exist_ok=True)
    base=Path(result.get('_base_dir',dest.parent))
    pending=result.get('pending_review_inputs',[])
    if isinstance(pending,dict): pending=list(pending.values())
    pending_by_id={}
    for entry in pending:
        if isinstance(entry,dict) and entry.get('row_id') and entry.get('status')=='pending':
            pending_by_id.setdefault(str(entry['row_id']),[]).append(str(entry.get('text','')))
    cards=[]; ids=set()
    quote_by_id={r['id']:r for r in result.get('quote_rows',[])}
    labels={'rows':'可计算','review':'待复核','excluded':'不单列'}
    for bucket in labels:
        for row in result.get(bucket,[]):
            row=quote_by_id.get(row['id'],row)
            row_id=str(row.get('id',''))
            if not row_id or row_id in ids: raise ValueError('复核卡片每项必须有唯一可见ID')
            ids.add(row_id)
            evidence=row.get('evidence') if isinstance(row.get('evidence'),dict) else {}
            image=_image(row.get('thumbnail'),base)
            thumbnail=(f'<details class="drawing"><summary>展开原图 / 局部图</summary><img src="{image}" alt="{escape(row_id,quote=True)} 图纸" loading="lazy"></details>' if image else '<p class="muted">没有可用缩略图，请回源文件查看。</p>')
            problems=[str(row[k]) for k in ('reason','review_reason','price_issue') if row.get(k)]
            problems.extend(str(v) for v in row.get('warnings',[]) if v)
            problems.extend(str(v.get('field',''))+'：'+str(v.get('reason','')) for v in row.get('evidence_warnings',[]) if isinstance(v,dict))
            if row.get('unverified_fields'): problems.append('未核实尺寸已留空：'+', '.join(row['unverified_fields']))
            problems.extend(str(v) for v in evidence.get('ambiguities',[]) if v)
            facts={k:row[k] for k in ('product_type','length_mm','height_mm','depth_mm','count','repeat_count','method','unit','quantity','measured_quantity','formula','rule_id') if k in row}
            sections=[('尺寸、数量与计量规则',facts),('尺寸读数及来源',evidence.get('dimension_readings',row.get('dimension_readings',[]))),
                      ('原生文字核对',evidence.get('source_checks',[])),('尺寸链与扣减',dict(chain_check=evidence.get('chain_check',[]),dimension_checks=evidence.get('dimension_checks',[]),adjustments=evidence.get('adjustments',[]),field_confirmations=evidence.get('field_confirmations',[]))),
                      ('来源',row.get('sources',[]))]
            section_html=''.join(f'<details><summary>{label}</summary><pre>{escape(_text(value))}</pre></details>' for label,value in sections)
            problem_html='<ul>'+''.join(f'<li>{escape(p)}</li>' for p in dict.fromkeys(problems))+'</ul>' if problems else '<p class="muted">无已记录问题；仍需核对尺寸归属和计量范围。</p>'
            search=' '.join(_text(row.get(k,'')) for k in ('id','name','space','product_type','reason'))
            prior='\n'.join(pending_by_id.get(row_id,[]))
            pending_html=f'<p class="pending">待处理反馈：{escape(prior)}</p>' if prior else ''
            cards.append(f'''<article class="card" data-bucket="{bucket}" data-search="{escape(search,quote=True)}">
<div class="card-head"><strong>{escape(row_id)}</strong><span class="badge {bucket}">{labels[bucket]}</span></div>
<h2>{escape(_text(row.get('name','未命名')))}</h2><p>{escape(_text(row.get('space','')))} · {escape(_text(row.get('status',labels[bucket])))}</p>
<p class="quantity">{escape(_text(row.get('quantity')))} <small>{escape(_text(row.get('unit','')))}</small></p>
{thumbnail}{problem_html}{section_html}{pending_html}<label>本项补充 / 更正意见（不会自动改报价）
<textarea data-row-id="{escape(row_id,quote=True)}" rows="3" placeholder="例如：请核对顶部总长；这里只填写意见，不会自动确认。"></textarea></label></article>''')
    metadata=json.dumps(dict(schema_version=1,kind='review_inputs',project_id=project_id,base_revision=revision),ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    title=escape(_text(result.get('project','安装报价复核')))
    document='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'">
<title>'''+title+''' · 复核卡片</title><style>
*{box-sizing:border-box}body{margin:0;padding:24px;background:#f2f5f7;color:#1d2b38;font:15px/1.65 system-ui,sans-serif}main{max-width:1280px;margin:auto}h1{font-size:25px;margin:0}header p{margin:6px 0}h2{font-size:20px;margin:8px 0}button,input,select,textarea{font:inherit;border:1px solid #c6d1db;border-radius:7px;padding:9px;background:white;color:inherit}button{cursor:pointer;background:#1559a5;color:#fff}textarea{width:100%;resize:vertical}label{display:block;margin-top:16px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.toolbar input{flex:1;min-width:180px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:18px}.card{background:white;padding:20px;border:1px solid #d8e1e7;border-radius:12px;min-width:0}.card[hidden]{display:none}.card-head{display:flex;justify-content:space-between;gap:8px}.badge{font-size:13px;padding:1px 8px;border-radius:10px;background:#e2eefb}.review{background:#fff0cc}.excluded{background:#e9ecef}.quantity{font-size:26px;font-weight:650;margin:10px 0}.quantity small{font-size:14px;font-weight:400}.muted{color:#637583}.pending{background:#fff2d5;padding:10px;white-space:pre-wrap}summary{cursor:pointer;padding:6px 0;font-weight:600}pre{overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 ui-monospace,monospace;background:#f5f7f9;padding:10px}img{display:block;width:100%;height:auto}.drawing[open]{overflow:auto;max-height:650px}.drawing img:active{width:auto;max-width:none}li{overflow-wrap:anywhere}footer{margin-top:20px;color:#637583}@media print{.toolbar,textarea,label{display:none}.cards{display:block}.card{break-inside:avoid;margin-bottom:16px}}
</style></head><body><main><header><h1>'''+title+''' · 复核卡片</h1><p>项目 '''+escape(project_id)+''' · 修订 '''+str(revision)+'''。原图数字一致不等于尺寸归属正确。</p><p>填写意见后下载 JSON，交回智能体逐项核对；本页面不会修改任何报价或自动确认待复核项。</p></header>
<div class="toolbar"><input id="search" type="search" placeholder="按 ID、名称、空间或问题搜索"><select id="filter"><option value="all">全部项目</option><option value="review">待复核</option><option value="rows">可计算</option><option value="excluded">不单列</option></select><button id="download" type="button">下载复核意见 JSON</button><span id="count" aria-live="polite"></span></div>
<div class="cards">'''+''.join(cards)+'''</div><footer>原生文字索引没有执行 OCR；无原生文字的扫描图仍需查看清晰局部。展开图像后可按住图片查看原尺寸。填写内容仅保留在当前页面，关闭前请下载。</footer></main>
<script id="review-metadata" type="application/json">'''+metadata+'''</script><script>
'use strict';
const metadata=JSON.parse(document.getElementById('review-metadata').textContent);
const search=document.getElementById('search'),filter=document.getElementById('filter');
function applyFilter(){let n=0;for(const card of document.querySelectorAll('.card')){const show=(filter.value==='all'||card.dataset.bucket===filter.value)&&card.dataset.search.toLowerCase().includes(search.value.toLowerCase());card.hidden=!show;if(show)n++;}document.getElementById('count').textContent=n+' 项';}
search.addEventListener('input',applyFilter);filter.addEventListener('change',applyFilter);applyFilter();
document.getElementById('download').addEventListener('click',()=>{const inputs=Array.from(document.querySelectorAll('textarea[data-row-id]')).map(node=>({row_id:node.dataset.rowId,text:node.value.trim()})).filter(row=>row.text);const payload={...metadata,inputs};const blob=new Blob([JSON.stringify(payload,null,2)+'\\n'],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='review-inputs-r'+metadata.base_revision+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
</script></body></html>'''
    dest.write_text(document,encoding='utf-8')
    return dest
