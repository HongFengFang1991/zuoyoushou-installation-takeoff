"""Local native-PDF evidence index. Numeric matches are not semantic verification.

Coordinates are normalized to page.render() orientation (top-left origin), including
inherited CropBox and /Rotate. No OCR, network, or model is used. PDFium APIs:
https://pypdfium2-team.github.io/pypdfium2/python_api.html#pypdfium2.PdfTextPage.get_charbox
https://pypdfium2-team.github.io/pypdfium2/python_api.html#pypdfium2.PdfTextPage.search
"""
import ctypes
import hashlib
import math
from pathlib import Path
import re
import unicodedata

NUMBER = re.compile(r'(?<![\d.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')
UNIT = re.compile(r'^\s*(mm|cm|m|毫米|厘米|米)(?![A-Za-z])', re.I)
CAUTION = '原生数字匹配只证明该页/区域有相同数字；尺寸归属、方向、柜体边界和重复实例仍需看图核对。'


def _hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''): h.update(part)
    return h.hexdigest()


def _number(value):
    if isinstance(value,bool): raise ValueError('数值不能为布尔值')
    n=float(value)
    if not math.isfinite(n): raise ValueError('数值必须有限')
    return int(n) if n.is_integer() else n


def _box(value):
    if not isinstance(value,(list,tuple)) or len(value)!=4: raise ValueError('box需要4个归一化坐标')
    b=[_number(x) for x in value]
    if not (0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1): raise ValueError('box坐标必须在0–1范围且面积大于0')
    return b


def _contains(box,token_box):
    x=(token_box[0]+token_box[2])/2; y=(token_box[1]+token_box[3])/2
    return box[0]<=x<=box[2] and box[1]<=y<=box[3]


def _tokens(text):
    # NFKC permits full-width digits; these characters preserve one-to-one indices.
    normalized=''.join(unicodedata.normalize('NFKC',c) if len(unicodedata.normalize('NFKC',c))==1 else c for c in text)
    for m in NUMBER.finditer(normalized):
        unit=UNIT.match(normalized[m.end():]); unit=unit.group(1).lower() if unit else ''
        value=_number(m.group().replace(',','')); factor={'m':1000,'米':1000,'cm':10,'厘米':10}.get(unit,1)
        yield m.start(),m.end(),dict(raw=text[m.start():m.end()],normalized=m.group().replace(',',''),value=value,
                                     unit=unit,value_mm=_number(value*factor))


def index_document_page(doc_path, page):
    """Return JSON-safe index for a 1-based page. ``box`` uses rendered orientation."""
    path=Path(doc_path).expanduser().resolve()
    if not isinstance(page,int) or isinstance(page,bool) or page<1: raise ValueError('page必须是从1开始的整数')
    result=dict(schema_version=1,file=path.name,page=page,source_hash=_hash(path),
                coordinate_system='normalized_rendered_top_left',rotation=0,numbers=[],text_available=False,
                text_status='image_no_native_text',text='',warnings=[],proof_note=CAUTION)
    if path.suffix.lower()!='.pdf':
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            if page>getattr(image,'n_frames',1): raise ValueError('页码超出图像帧数')
            image.seek(page-1); oriented=ImageOps.exif_transpose(image)
            result['size']=list(oriented.size)
        result['warnings']=['图片没有原生文字索引；需局部看图，未执行OCR。']
        return result
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    with pdfium.PdfDocument(str(path)) as pdf:
        if page>len(pdf): raise ValueError('页码超出PDF页数')
        pdfpage=pdf[page-1]
        try:
            result.update(size=list(pdfpage.get_size()),rotation=pdfpage.get_rotation(),page_box=list(pdfpage.get_bbox()))
            # Device mapping includes inherited crop and rotation exactly as rendering does.
            width,height=pdfpage.get_size(); scale=1000000/max(width,height)
            dw,dh=max(1,round(width*scale)),max(1,round(height*scale))
            textpage=pdfpage.get_textpage()
            try:
                # PDFium's extracted string offsets can differ from character indices.
                # Read its character array directly so get_charbox() indices remain exact.
                chars=[]
                for i in range(textpage.count_chars()):
                    cp=raw.FPDFText_GetUnicode(textpage,i)
                    chars.append(chr(cp) if cp and cp<=0x10ffff else '\ufffd')
                text=''.join(chars); result['text']=text
                result['text_available']=any(c.isalnum() and c!='\ufffd' for c in text)
                result['text_status']='native_text' if result['text_available'] else 'pdf_no_native_text'
                for start,end,token in _tokens(text):
                    boxes=[]
                    for pos in range(start,end):
                        try: boxes.append(textpage.get_charbox(pos))
                        except Exception: pass
                    if not boxes: continue
                    points=[]
                    for l,b,r,t in boxes:
                        for x,y in ((l,b),(l,t),(r,b),(r,t)):
                            dx,dy=ctypes.c_int(),ctypes.c_int()
                            ok=raw.FPDF_PageToDevice(pdfpage,0,0,dw,dh,0,x,y,dx,dy)
                            if not ok: raise ValueError('PDF坐标转换失败')
                            points.append((dx.value/dw,dy.value/dh))
                    x0=min(p[0] for p in points); x1=max(p[0] for p in points)
                    y0=min(p[1] for p in points); y1=max(p[1] for p in points)
                    # Native text outside the visible crop cannot be used as drawn proof.
                    if x1<=0 or y1<=0 or x0>=1 or y0>=1: continue
                    box=[round(max(0,x0),6),round(max(0,y0),6),round(min(1,x1),6),round(min(1,y1),6)]
                    if box[0]>=box[2] or box[1]>=box[3]: continue
                    token.update(id=f'p{page}:c{start}-{end}',char_start=start,char_count=end-start,box=box,
                                 context=' '.join(text[max(0,start-24):min(len(text),end+24)].split()))
                    result['numbers'].append(token)
            finally: textpage.close()
        finally: pdfpage.close()
    if not result['text_available']: result['warnings'].append('该PDF页没有可用原生文字；需局部看图，未执行OCR。')
    elif not result['numbers']: result['warnings'].append('该页有文字但没有可定位数字，不能独立校验读数。')
    return result


def query_index(index, query='', limit=30, region=None):
    """Compact search, omitting full page text; region is an optional normalized box."""
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=200: raise ValueError('limit范围为1–200')
    box=_box(region) if region is not None else None
    words=str(query or '').casefold().split()
    matches=[]
    for token in index.get('numbers',[]):
        if box and not _contains(box,token['box']): continue
        haystack=' '.join(str(token.get(k,'')) for k in ('id','raw','normalized','context')).casefold()
        if words and not all(word in haystack for word in words): continue
        matches.append(token)
    # Exact numeric/token queries should put that token before its neighbours' contexts.
    matches.sort(key=lambda token: 0 if str(query) in {str(token.get('value')),token.get('raw'),token.get('id')} else 1)
    return dict(file=index.get('file'),page=index.get('page'),source_hash=index.get('source_hash'),
                coordinate_system=index.get('coordinate_system'),text_status=index.get('text_status'),
                total_matches=len(matches),truncated=len(matches)>limit,
                numbers=[{k:t[k] for k in ('id','raw','value','unit','value_mm','box','context') if k in t} for t in matches[:limit]],
                note=CAUTION if index.get('text_available') else '没有原生文字证据；请打开相关区域图像核对。')


def _lookup(indexes, source, source_index):
    for key in ((source.get('file'),source.get('page')), f"{source.get('file')}:{source.get('page')}", source_index, str(source_index)):
        if key in indexes: return indexes[key]
    return None


def verify_readings(item, index_by_source):
    """Check declared native readings; never upgrades visual/semantic confidence.

    ``item.evidence.dimension_readings`` (or top-level dimension_readings) contains
    {field,value_mm,source_index,text,region,box?}. source_index is zero-based into
    item.sources; role=context may never prove a measurement. Missing native text
    is reported unavailable, not as an OCR or correctness result.
    """
    result=dict(checks=[],issues=[],warnings=[],checked=0)
    evidence=item.get('evidence',{})
    if not isinstance(evidence,dict):
        result['issues'].append('evidence必须为对象'); return result
    readings=evidence.get('dimension_readings',item.get('dimension_readings',[]))
    if not isinstance(readings,list):
        result['issues'].append('dimension_readings必须为数组'); return result
    sources=item.get('sources',[])
    if not isinstance(sources,list):
        result['issues'].append('sources必须为数组'); return result
    for reading in readings:
        check=dict(field=reading.get('field') if isinstance(reading,dict) else None,status='conflict',reason='')
        try:
            if not isinstance(reading,dict): raise ValueError('读数必须为对象')
            field=reading.get('field')
            if not isinstance(field,str) or not field: raise ValueError('读数缺少field')
            si=reading.get('source_index'); check['source_index']=si
            if not isinstance(si,int) or isinstance(si,bool) or not 0<=si<len(sources): raise ValueError('source_index未指向有效来源')
            source=sources[si]
            if not isinstance(source,dict): raise ValueError('来源必须为对象')
            if source.get('role')!='measurement': raise ValueError('读数来源必须标记role=measurement，context不能作尺寸依据')
            value=_number(reading.get('value_mm'))
            if value<=0: raise ValueError('value_mm必须大于0')
            text=reading.get('text','')
            if not isinstance(text,str) or not text.strip(): raise ValueError('读数缺少原文text')
            declared=[t for _,_,t in _tokens(text)]
            if not any(abs(t['value_mm']-value)<1e-7 for t in declared): raise ValueError('value_mm与声明的原文text不一致')
            box=_box(reading['box']) if reading.get('box') is not None else None
            idx=_lookup(index_by_source,source,si)
            if idx and (idx.get('file')!=source.get('file') or idx.get('page')!=source.get('page')):
                raise ValueError('原生索引与声明来源文件/页码不一致')
            expected_hash=source.get('source_hash',source.get('sha256'))
            if idx and expected_hash and idx.get('source_hash')!=expected_hash:
                raise ValueError('原生索引源文件哈希与声明来源不一致')
            if not idx or not idx.get('text_available') or not idx.get('numbers'):
                check.update(status='unavailable',reason='该来源无可用原生数字索引；需局部看图，不能用visual标记替代核验')
                result['warnings'].append(f'{field}：'+check['reason'])
            else:
                result['checked']+=1
                candidates=[t for t in idx['numbers'] if not box or _contains(box,t['box'])]
                matches=[t for t in candidates if abs(t.get('value_mm',t['value'])-value)<1e-7]
                if not matches: raise ValueError('声明读数在指定原生文字页/区域中未找到；需检查抄录、单位或区域')
                check.update(status='matched',reason='原生数字匹配；尚未证明尺寸归属和测量语义',token_ids=[t['id'] for t in matches])
                if not box: result['warnings'].append(f'{field}：未指定box，仅在整页找到相同数字。')
        except (TypeError,ValueError,KeyError) as exc:
            check.update(status='conflict',reason=str(exc)); result['issues'].append(f"{check.get('field') or '读数'}：{exc}")
        result['checks'].append(check)
    if any(c['status']=='matched' for c in result['checks']): result['warnings'].append(CAUTION)
    return result
