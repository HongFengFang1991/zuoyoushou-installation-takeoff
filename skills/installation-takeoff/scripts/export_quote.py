"""Portable WorkBuddy XLSX writer and conservative manual-edit reader.

The target hosts do not ship Codex's artifact runtime. Uses public XlsxWriter;
never runs spreadsheet macros or external formulas. Generated files remain native XLSX.
"""
from collections import OrderedDict
import copy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
import zipfile
import uuid
import math
import unicodedata

from prepare_quote import HEADERS, METHODS, detail_values, rounded
from workbook_reconcile import reconcile_previous, identity_for, merge_pending, VERSION
from quotation_view import quotation_view, review_note, pricing_scope

SKILL = Path(__file__).resolve().parent.parent
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


def drawing_pages(row, include_files=False):
    """Physical source-page indexes (1-based); never infer from product IDs."""
    groups = OrderedDict()
    for source in (row.get('sources') if isinstance(row.get('sources'), list) else []):
        if not isinstance(source, dict):
            continue
        page = source.get('page')
        if isinstance(page, int) and not isinstance(page, bool) and page > 0:
            groups.setdefault(source.get('file') or '未标文件', set()).add(page)
    if not groups:
        return '待补页码'
    show_files = include_files or len(groups) > 1
    return '\n'.join((name + '：' if show_files else '') + '第' +
                     '、'.join(map(str, sorted(pages))) + '页'
                     for name, pages in groups.items())


def page_overview(result, rows):
    documents = result.get('documents', [])
    pdfs = [d for d in documents if d['file'].lower().endswith('.pdf')]
    selected = pdfs or documents
    limits = {d['file']: d['page_count'] for d in selected}
    pages = {name: set() for name in limits}
    for row in rows:
        if row.get('quantity') is None:
            continue
        for source in row.get('sources', []) if isinstance(row.get('sources'), list) else []:
            if not isinstance(source, dict): continue
            name, page = source.get('file'), source.get('page')
            if isinstance(name, str) and name in limits and type(page) is int and 1 <= page <= limits[name] and source.get('role','measurement') == 'measurement':
                pages[name].add(page)
    total = sum(limits.values()); used = sum(map(len, pages.values()))
    listing = '；'.join((name+'：' if len(selected)>1 else '') + '、'.join(map(str, sorted(values))) for name, values in pages.items() if values) or '无'
    return f'{"PDF总页数" if pdfs else "图纸资料总页数"}：{total}页；参与计算：{used}页（含暂计项，按取量来源去重）；页码：{listing}'


def group_key(row):
    scope = pricing_scope(row)
    descriptor = json.dumps([row['product_type'], row.get('cabinet_subtype', ''), row.get('method'), row.get('unit'), row.get('rule_id'), scope], ensure_ascii=False)
    return 'g-' + hashlib.sha256(descriptor.encode()).hexdigest()[:24]


def groups_for(rows):
    groups = OrderedDict()
    for i, row in enumerate(rows, 7):
        key = group_key(row)
        groups.setdefault(key, []).append((i, row))
    return groups


def source_note(row, number, include_files=False):
    """One source entity per note; formula follows visible dimensions/quantity."""
    method=row.get('method'); n=number
    dims=[]; pieces=[]
    if method == 'count':
        dimension='按数量'; dim_formula='"按数量"'
    else:
        required={'plan_area':{'length_mm','depth_mm'},'projection_area':{'length_mm','height_mm'},'length':{'length_mm'}}.get(method,set())
        for field,label,col in [('length_mm','长','C'),('height_mm','高','D'),('depth_mm','深/宽','E')]:
            if field not in required and row.get(field) is None: continue
            value=row.get(field)
            dims.append(label+(format(value,'g') if isinstance(value,(float,int)) else '待补'))
            pieces.append(f'IF(ISNUMBER({col}{n}),"{label}"&TEXT({col}{n},"General"),"{label}待补")')
        dimension='×'.join(dims)+'mm' if dims else '详见明细依据'
        dim_formula=('&"×"&'.join(pieces)+'&"mm"') if pieces else '"详见明细依据"'
    repeat=row.get('repeat_count',1)
    repeat_text=f' ×{repeat}套' if isinstance(repeat,(int,float)) and repeat>1 else ''
    qty=row.get('quantity'); unit='㎡' if row.get('unit')=='平米' else row.get('unit') or ''
    amount=(format(qty,'.0f' if method=='count' else '.3f')+unit) if isinstance(qty,(int,float)) else '数量待补'
    status='【待复核·暂计】' if row.get('provisional') and qty is not None else '【待复核·未计量】' if row.get('provisional') else ''
    pages=drawing_pages(row,include_files).replace('\n','；')
    cache=f"[{row['id']}] {row.get('space') or '未标空间'} / {row.get('name','')}｜{dimension}{repeat_text}｜{amount}{status}｜{pages}"
    formula=(f'="["&O{n}&"] "&IF(A{n}="","未标空间",A{n})&" / "&B{n}&"｜"&{dim_formula}&"{repeat_text}｜"&'
             f'IF(ISNUMBER(H{n}),TEXT(H{n},"{ "0" if method=="count" else "0.000" }")&IF(G{n}="平米","㎡",G{n}),"数量待补")&'
             f'IF(LEFT(K{n},3)="待复核",IF(ISNUMBER(H{n}),"【待复核·暂计】","【待复核·未计量】"),"")&"｜"&SUBSTITUTE(R{n},CHAR(10),"；")')
    return cache,formula


def note_chunks(notes, width=96):
    """Continue long groups instead of clipping at Excel's 409-point row cap."""
    chunks=[]; current=[]; lines=0
    for entry in notes:
        size=sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in entry[1])
        used=max(1,math.ceil(size/width))
        if current and lines+used>18:
            chunks.append((current,lines)); current=[]; lines=0
        current.append(entry); lines+=used
    if current: chunks.append((current,lines))
    return chunks


def read_cells(path):
    """Read stored values/formulas only. Never evaluate user-provided Excel formulas."""
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 250_000_000:
            raise ValueError('工作簿解压体积异常，请先另存精简xlsx')
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS):
                strings.append(''.join(n.text or '' for n in si.iter('{'+NS['s']+'}t')))
        rels = {e.attrib['Id']: e.attrib['Target'] for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        book = ET.fromstring(z.read('xl/workbook.xml')); result = {}
        for sheet in book.findall('s:sheets/s:sheet', NS):
            target = rels[sheet.attrib[REL]]
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            cells = {}
            for c in ET.fromstring(z.read(member)).findall('.//s:sheetData/s:row/s:c', NS):
                t = c.get('t'); v = c.find('s:v', NS); f = c.find('s:f', NS)
                text = v.text if v is not None and v.text is not None else ''
                if t == 's':
                    value = strings[int(text)]
                elif t == 'inlineStr':
                    value = ''.join(n.text or '' for n in c.findall('.//s:t', NS))
                elif t in {'str', 'e', 'b'} or text == '':
                    value = text
                else:
                    value = float(text)
                cells[c.attrib['r']] = dict(value=value, formula=f.text if f is not None else None)
            result[sheet.attrib['name']] = cells
        return result


def export_workbook(result, path, manual=None):
    import xlsxwriter
    from PIL import Image
    path = Path(path); manual = manual or {}
    result = copy.deepcopy(result)
    # Reconciliation can be consumed before or after the runtime commits patches.
    # Derive all formulas from effective current facts, never stale workbook caches.
    for collection in ('rows', 'review', 'excluded'):
        for row in result.get(collection, []):
            row.update(manual.get('item_patches', {}).get(row['id'], {}))
    pending = merge_pending(result.get('pending_review_inputs', []), manual.get('pending_review_inputs', []))
    pending_by_id = {p['row_id']: p for p in pending if p.get('status') == 'pending'}
    warnings = list(dict.fromkeys(manual.get('warnings', []) + result.get('warnings', []) + result.get('coverage_issues', [])))
    if pending_by_id and not any('人工确认' in warning for warning in warnings):
        warnings.append('人工反馈待结构化核验，不自动解除待复核状态；可计算项已按当前值暂计')
    result.update(quotation_view(result))
    rows = result['quote_rows']; groups = groups_for(rows)
    meta = dict(version=VERSION, summary_group_column='H', project=result['project'], **identity_for(result), workbook_id=str(uuid.uuid4()),
                rows={}, groups={}, review={}, thumbnails=[], effective_prices={},
                pending_review_inputs=pending, warnings=warnings,
                thumbnail_issues=result.get('thumbnail_issues', []),
                evidence_policy=result.get('evidence_policy', 1), evidence_mode=result.get('evidence_mode'),
                visual_verified=result.get('visual_verified', False),
                coverage={key: result.get(key) for key in ('coverage_complete', 'page_coverage_complete',
                    'product_coverage_complete', 'accessory_coverage_complete', 'missing_pages', 'unreadable_pages', 'coverage_issues')})
    names = {'cabinet':'柜体安装', 'wall_cabinet':'吊柜/镜柜安装', 'base_cabinet':'地柜/浴室柜安装',
             'drawer':'抽屉安装', 'light_strip':'灯带安装', 'tatami':'榻榻米安装', 'desk':'桌台安装',
             'wall_panel':'墙板安装', 'countertop':'台面安装', 'door':'木门安装',
             'pull_basket':'拉篮安装', 'dish_basket':'碗篮安装', 'seasoning_basket':'调味篮安装', 'custom':'自定义服务'}
    with xlsxwriter.Workbook(str(path), {'strings_to_formulas':False, 'strings_to_urls':False}) as wb:
        wb.set_properties({'title':result['project']+'安装报价表', 'comments':'installation-takeoff '+VERSION})
        for key in ('project_id', 'source_fingerprint', 'revision', 'workbook_id'):
            wb.set_custom_property('takeoff_'+key, str(meta[key]))
        wb.set_custom_property('takeoff_version', VERSION)
        wb.set_calc_mode('auto')
        base = dict(font_name='Microsoft YaHei', font_size=10, font_color='#163650', valign='vcenter')
        title = wb.add_format(dict(base, bold=True, font_size=20, font_color='white', bg_color='#087CBD'))
        section = wb.add_format(dict(base, bold=True, font_color='white', bg_color='#087CBD'))
        header = wb.add_format(dict(base, bold=True, font_color='white', bg_color='#163650', text_wrap=True, align='center'))
        info = wb.add_format(dict(base, font_size=10, text_wrap=True))
        total = wb.add_format(dict(base, bold=True, font_color='white', bg_color='#163650', num_format='#,##0.00'))
        formats = {}
        for stripe in (0,1,'ok','review'):
            for kind, code in [('text',None),('qty','0.000'),('count','0'),('money','#,##0.00'),('price','#,##0.00')]:
                cfg = dict(base, bg_color='#FFF1CF' if kind=='price' else ('#EAF4FA' if stripe else 'white'),
                           bottom=1, bottom_color='#DCE7EE', text_wrap=kind=='text')
                if stripe in ('ok','review'):
                    cfg['bg_color'] = '#E2F0D9' if stripe=='ok' else '#FCE4D6'
                    if kind=='price': cfg['font_color']='#0070C0'
                if code: cfg.update(num_format=code, align='right')
                formats[stripe,kind] = wb.add_format(cfg)
        logo = SKILL / 'assets/zuoyoushou-logo.png'
        def add_image(ws, r, c, image_path, maxw, maxh, row_id=None):
            if not image_path or not Path(image_path).is_file():
                return False
            with Image.open(image_path) as im:
                w,h = im.size
            scale = min(maxw/w, maxh/h)
            ws.insert_image(r,c,str(image_path),dict(x_scale=scale,y_scale=scale,x_offset=6,y_offset=5,
                                                    object_position=1,description=row_id or '左右手Logo'))
            if row_id:
                meta['thumbnails'].append(dict(sheet=ws.name,row_id=row_id,cell=f'{chr(65+c)}{r+1}',image=str(image_path)))
            return True
        def top(ws, lastcol, subtitle):
            ws.hide_gridlines(2); ws.freeze_panes(6,2)
            ws.set_row(0,26); ws.set_row(1,26); ws.set_row(2,25); ws.set_row(3,34)
            ws.merge_range(0,0,1,1,'')
            add_image(ws,0,0,logo,200,62)
            ws.merge_range(0,2,1,lastcol,'左右手 · 全屋定制安装报价表',title)
            project_text=f"项目：{result['project']}　　报价日期：{date.today()}　　人民币 / 元"
            if ws.name == '报价明细':
                project_text += '\n'+page_overview(result, rows)+'\n颜色：浅绿色＝依据齐全；浅橙色＝待复核（可计算项默认暂计）；蓝字单价格可填写。'
                ws.set_row(2,65)
            ws.merge_range(2,0,2,lastcol,project_text,info)
            ws.merge_range(3,0,3,lastcol,subtitle,info)
            ws.set_landscape(); ws.fit_to_pages(1,0); ws.repeat_rows(0,5)
            ws.set_margins(.25,.25,.3,.3); ws.set_paper(8)
        unresolved = len(result['review']); missing = len(result['missing_pages']) + len(result['unreadable_pages'])
        state_label = ('合成演示，非真实报价' if result['evidence_mode']=='synthetic' else
                       '文字试算，未核验原图' if not result['visual_verified'] else '安装服务报价草稿')
        prefix = f'{state_label}；含待复核{unresolved}项（可计算项已暂计） / 未覆盖{missing}页 / 未计量{result["quote_unmeasured_count"]}项。合计含已填价的暂计项目。'
        if result.get('evidence_policy', 1) < 3:
            prefix += '旧证据策略，仅供试算。'
        if result.get('product_coverage_complete') is False or result.get('accessory_coverage_complete') is False:
            prefix += '产品/配件覆盖尚未核对完整。'
        if pending_by_id:
            prefix += f'人工确认待处理{len(pending_by_id)}项。'
        detail = wb.add_worksheet('报价明细'); summary = wb.add_worksheet('报价汇总')
        top(detail,18,prefix); top(summary,6,prefix)
        widths = [16,24,12,12,15,17,11,13,13,16,18,35,25,48,15,14]
        for i,w in enumerate(widths): detail.set_column(i,i,w)
        detail.set_column(14,16,15,None,{'hidden':True})
        # Append a visible column while retaining existing price/ID positions for
        # three-way import of both old and new workbooks.
        detail.set_column(17,17,28 if len(result['documents']) > 1 else 18)
        detail.set_column(18,18,30)
        detail.set_column(19,19,15,None,{'hidden':True})
        for c,w in enumerate([28,22,12,16,16,20]): summary.set_column(c,c,w)
        summary.set_column(6,6,100)
        summary.set_column(7,7,5,None,{'hidden':True})
        for a,b,text in [(0,1,'01  产品信息'),(2,4,'02  图纸尺寸 / mm'),(5,9,'03  工程量与费用'),(10,18,'04  核对信息与图纸')]:
            detail.merge_range(4,a,4,b,text,section)
        summary.merge_range(4,0,4,6,'01  服务项目与报价',section)
        detail.write_row(5,0,HEADERS+['价格状态','组编号','图纸页码','人工复核意见','汇总来源说明'],header); detail.set_row(5,30)
        summary.write_row(5,0,['服务项目','计量口径','单位','单价（元）','数量','金额','备注（空间 / 产品 / 尺寸 / 数量 / 页码）','组编号'],header)
        summary.set_row(5,30)
        group_cells = {}; group_values = {}; summary_plan=[]; next_summary_row=7
        for key,members in groups.items():
            sr=next_summary_row
            chunks=note_chunks([(n,source_note(r,n,len(result['documents'])>1)[0]) for n,r in members])
            summary_plan.append((sr,key,members,chunks))
            next_summary_row+=len(chunks)
            prices = {r.get('unit_price') for _,r in members}
            price = next(iter(prices)) if len(prices)==1 and None not in prices else ''
            group_cells[key] = sr; group_values[key] = price
            meta['groups'][key] = dict(price=price,members=[r['id'] for _,r in members], row_number=sr)
        row_amounts = {}; row_prices = {}
        for n,row in enumerate(rows,7):
            key = group_key(row); sr = group_cells[key]; stripe='review' if row.get('provisional') else 'ok'
            tf = formats[stripe,'text']; pf=formats[stripe,'price']; mf=formats[stripe,'money']
            vals = detail_values(row)
            # Keep editable notes separate from read-only evidence/status explanations.
            note = row.get('remark', '') or ''
            system_note = review_note(row)
            vals[11] = '\n'.join(x for x in (system_note, note) if x)
            explanations = '；'.join(str(row.get(k, '')) for k in ('reason', 'price_issue', 'user_confirmation') if row.get(k))
            vals[13] = f"编号：{row['id']}；" + vals[13] + ('；'+explanations if explanations else '')
            for warning in row.get('evidence_warnings', []):
                field = warning.get('field', '')
                original = row.get('unverified_fields', {}).get(field)
                vals[13] += f"；未核验字段{field}={original}（未用于本项取量）：{warning.get('reason', '')}"
            note_cache,note_formula=source_note(row,n,len(result['documents'])>1)
            detail.write_formula(n-1,19,note_formula,tf,note_cache)
            detail.set_row(n-1,125 if row.get('provisional') else 95)
            detail.write(n-1,17,drawing_pages(row, len(result['documents']) > 1),tf)
            for c in list(range(0,8))+[10,11,13,14]:
                value=vals[c]
                detail.write(n-1,c,value if value is not None else '',formats[stripe,'count' if row['method']=='count' else 'qty'] if c==7 else tf)
            detail.write(n-1,12,'',tf)
            if not add_image(detail,n-1,12,row.get('thumbnail'),172,112,row['id']):
                detail.write(n-1,12,'待补缩略图',tf)
            if row['id'] in pending_by_id:
                detail.write(n-1,13,vals[13]+'；待处理反馈：'+pending_by_id[row['id']].get('text',''),tf)
            feedback=pending_by_id.get(row['id'],{}).get('text','')
            detail.write(n-1,18,feedback,tf)
            if row.get('provisional') or row['id'] in pending_by_id:
                meta['review'][row['id']]=dict(row_number=n,text=feedback,sheet='报价明细',column='S',id_column='O')
            group_price=group_values[key]
            # Only use category defaults if every saved row used that same price.
            saved_prices = {r.get('unit_price') for _,r in groups[key]}
            single_default = len(saved_prices)==1
            if not single_default and row.get('unit_price') is not None:
                price=row['unit_price']; pfml=None
            else:
                price=group_price
                pfml=f'=IF(ISNUMBER(\'报价汇总\'!D{sr}),\'报价汇总\'!D{sr},"")'
            if pfml: detail.write_formula(n-1,8,pfml,pf,price)
            else: detail.write(n-1,8,price,pf)
            amount = rounded(Decimal(str(row['quantity']))*Decimal(str(price)),2) if isinstance(price,(int,float)) and row.get('quantity') is not None else ''
            detail.write_formula(n-1,9,f'=IF(COUNT(H{n},I{n})<2,"",ROUND(H{n}*I{n},2))',mf,amount)
            detail.write_formula(n-1,15,f'=IF(COUNT(H{n},I{n})=2,"已填价","待补数量或价格")',tf,'已填价' if amount!='' else '待补数量或价格')
            detail.write(n-1,16,key)
            row_amounts[n]=amount; row_prices[n]=price
            meta['effective_prices'][row['id']] = price if price != '' else None
            meta['rows'][row['id']]=dict(cells={chr(65+c): (vals[c] if vals[c] is not None else '') for c in range(12)},
                                         unit=row['unit'],price_formula=pfml[1:] if pfml else None,price_value=price,
                                         input_price=row.get('unit_price'), remark=note, system_note=system_note, row_number=n, group_id=key)
        for sr,key,members,chunks in summary_plan:
            stripe='review' if any(r.get('provisional') for _,r in members) else 'ok'
            tf=formats[stripe,'text']; first=members[0][1]
            label=names.get(first['product_type'],first['name'])
            if first.get('cabinet_subtype') == 'sideboard':
                label='餐边柜安装'
            scope=pricing_scope(first)
            if scope!='默认': label += '（'+scope+'）'
            for offset,(entries,line_count) in enumerate(chunks):
                target=sr-1+offset
                summary.set_row(target,min(409,max(37,line_count*19+10)))
                summary.write_formula(target,6,'='+'&CHAR(10)&'.join(f"'报价明细'!T{n}" for n,_ in entries),tf,'\n'.join(text for _,text in entries))
                if offset:
                    summary.write_row(target,0,[label+'（来源续）','','','','',''],tf)
            summary.write_row(sr-1,0,[label,METHODS.get(first.get('method'),'待明确'),first.get('unit') or '待明确'],tf)
            summary.write(sr-1,3,group_values[key],formats[stripe,'price'])
            last=6+len(rows)
            group_range=f"'报价明细'!$Q$7:$Q${last}"
            qty_range=f"'报价明细'!$H$7:$H${last}"
            amount_range=f"'报价明细'!$J$7:$J${last}"
            price_status_range=f"'报价明细'!$P$7:$P${last}"
            quantities=[r['quantity'] for _,r in members if r.get('quantity') is not None]
            qty=rounded(sum((Decimal(str(q)) for q in quantities),Decimal(0))) if quantities else ''
            amounts=[row_amounts[n] for n,_ in members if row_amounts[n]!='']
            amount=rounded(sum((Decimal(str(a)) for a in amounts),Decimal(0)),2) if amounts else ''
            summary.write_formula(sr-1,4,f'=IF(COUNTIFS({group_range},H{sr},{qty_range},">0")=0,"",ROUND(SUMIF({group_range},H{sr},{qty_range}),3))',formats[stripe,'count' if first['method']=='count' else 'qty'],qty)
            summary.write_formula(sr-1,5,f'=IF(COUNTIFS({group_range},H{sr},{price_status_range},"已填价")=0,"",ROUND(SUMIF({group_range},H{sr},{amount_range}),2))',formats[stripe,'money'],amount)
            summary.write(sr-1,7,key)
        end=6+len(rows); send=next_summary_row-1
        amount_values=[a for a in row_amounts.values() if a!='']
        subtotal=rounded(sum((Decimal(str(a)) for a in amount_values),Decimal(0)),2) if amount_values else ''
        for ws,last,amount_col,last_col in [(detail,end,9,18),(summary,send,5,6)]:
            ws.merge_range(last,0,last,amount_col-1,'合计',total); ws.set_row(last,29)
            if last>=7:
                col=chr(65+amount_col)
                ws.write_formula(last,amount_col,f'=IF(COUNT({col}7:{col}{last})=0,"",ROUND(SUM({col}7:{col}{last}),2))',total,subtotal)
            else: ws.write(last,amount_col,'',total)
            for c in range(amount_col+1,last_col+1): ws.write(last,c,'',total)
            ws.print_area(0,0,last,last_col)
        if rows:
            for ws,last_col in [(detail,13),(summary,6)]:
                cache=f'{prefix} 待填价{sum(v=="" for v in row_prices.values())}项。'
                formula=f'="{prefix}"&" 待填价"&({len(rows)}-COUNT(\'报价明细\'!I7:I{end}))&"项。"'
                ws.write_formula(3,0,formula,info,cache)
            detail.data_validation(6,8,end-1,8,{'validate':'decimal','criteria':'>=','value':0,'ignore_blank':True,'error_title':'单价无效','error_message':'请填写非负单价；未知留空，免费填0。'})
        if groups:
            for sr in group_cells.values():
                summary.data_validation(sr-1,3,sr-1,3,{'validate':'decimal','criteria':'>=','value':0,'ignore_blank':True})
    meta['baseline_cells'] = read_cells(path)
    return meta
