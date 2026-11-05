"""Conservative, identity-bound three-way XLSX import; never evaluates formulas."""
import copy
import hashlib
import json
import math
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET
import zipfile

VERSION = '1.4.1'
SHEETS = {'报价明细', '报价汇总'}
V14_SHEETS = SHEETS | {'资料清单'}
LEGACY_SHEETS = V14_SHEETS | {'待复核'}
IDENTITY_KEYS = ('project_id', 'source_fingerprint', 'revision', 'workbook_id')
EMPTY = {'value': '', 'formula': None}


def identity_for(result):
    try:
        project_id = str(uuid.UUID(result['project_id']))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError('v1.3导出需要真实项目UUID；请先初始化或恢复项目，不能仅凭同名项目绑定') from None
    fingerprint = result.get('source_fingerprint')
    revision = result.get('revision')
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('导出缺少有效源文件指纹')
    if type(revision) is not int or revision < 0:
        raise ValueError('导出缺少有效项目修订号')
    return dict(project_id=project_id, source_fingerprint=fingerprint, revision=revision)


def workbook_identity(path):
    with zipfile.ZipFile(path) as z:
        if 'docProps/custom.xml' not in z.namelist():
            return {}
        props = ET.fromstring(z.read('docProps/custom.xml'))
    values = {p.get('name'): next(iter(p)).text for p in props if len(p)}
    return {key: values.get('takeoff_'+key) for key in IDENTITY_KEYS}


def same_cell(first, second):
    """Ignore formula caches: Excel/WPS may recalculate them without a user edit."""
    first = first or EMPTY
    second = second or EMPTY
    if first.get('formula') or second.get('formula'):
        return first.get('formula') == second.get('formula')
    return first.get('value', '') == second.get('value', '')


def price_value(cell, label):
    value = cell.get('value', '')
    if cell.get('formula'):
        raise ValueError(label+'单价公式已修改；请填非负数或空白，不读取外部公式或未重算缓存')
    if value == '':
        return None
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError(label+'单价须为非负数或空白')
    return value


def text_value(cell, label):
    if cell.get('formula'):
        raise ValueError(label+'包含公式，请改为普通文本后接续')
    value = cell.get('value', '')
    return '' if value is None else str(value)


def all_items(result):
    items = {r['id']: r for r in result.get('all_items', [])}
    for section in ('rows', 'review', 'excluded', 'quote_rows'):
        items.update({r['id']: r for r in result.get(section, [])})
    return items


def merge_pending(*collections):
    pending = []
    seen = set()
    for collection in collections:
        for entry in collection or []:
            key = entry.get('id') or (entry.get('row_id'), entry.get('text'), entry.get('source'), entry.get('cell'))
            if key not in seen:
                pending.append(copy.deepcopy(entry))
                seen.add(key)
    return pending


def reconcile_previous(path, result):
    from export_quote import read_cells, group_key
    path = Path(path)
    manifest_path = path.with_suffix('.manifest.json')
    if not manifest_path.is_file():
        raise ValueError('接续人工Excel需要同目录同名.manifest.json；缺少基准时保留原表，逐行对照后把确认内容另存新批次')
    baseline = json.loads(manifest_path.read_text(encoding='utf-8'))
    if baseline.get('version') not in {'1.3.0', '1.4.0', VERSION} or not baseline.get('project_id') or not baseline.get('baseline_cells'):
        raise ValueError('旧版工作簿缺少项目UUID/完整基准，不能自动绑定；请恢复原项目，保留旧表逐行手工对照，将确认修改存为新批次后导出v1.3表')
    current_identity = identity_for(result)
    if any(baseline.get(key) != current_identity[key] for key in ('project_id', 'source_fingerprint')):
        raise ValueError('人工表项目UUID或源文件指纹不匹配；同名项目、相同行ID不能作为接续依据')
    embedded = workbook_identity(path)
    if any(str(baseline.get(key)) != embedded.get(key) for key in IDENTITY_KEYS):
        raise ValueError('工作簿内嵌标识与manifest配对不一致；请恢复这份工作簿的同名基准文件')
    if type(baseline.get('revision')) is not int or baseline['revision'] > current_identity['revision']:
        raise ValueError('人工表来自较新项目修订；请先恢复对应项目状态再接续')
    cells = read_cells(path)
    expected_sheets = LEGACY_SHEETS if baseline['version'] == '1.3.0' else V14_SHEETS if baseline['version']=='1.4.0' else SHEETS
    group_column = 'G' if baseline['version'] == '1.3.0' else 'H'
    if set(cells) != expected_sheets or set(baseline['baseline_cells']) != expected_sheets:
        raise ValueError('人工表页签结构有变化，请先逐项核对')
    editable = set()
    for rid, old in baseline['rows'].items():
        number = old['row_number']
        if cells['报价明细'].get(f'O{number}', {}).get('value') != rid:
            raise ValueError(f'明细{rid}的行ID缺失/变化；排序、插行或删除后请先按ID逐行核对')
        editable.update({('报价明细', f'I{number}'), ('报价明细', f'L{number}')})
    for key, old in baseline['groups'].items():
        number = old['row_number']
        if cells['报价汇总'].get(f'{group_column}{number}', {}).get('value') != key:
            raise ValueError('汇总组ID缺失/变化，请先核对分组和行结构')
        editable.add(('报价汇总', f'D{number}'))
    for rid, old in baseline.get('review', {}).items():
        number = old['row_number']
        review_sheet=old.get('sheet','待复核'); review_column=old.get('column','H'); id_column=old.get('id_column','A')
        if cells[review_sheet].get(f'{id_column}{number}', {}).get('value') != rid:
            raise ValueError('待复核行ID缺失/变化，请先核对')
        editable.add((review_sheet, f'{review_column}{number}'))
    changes = []
    for sheet in sorted(expected_sheets):
        before = baseline['baseline_cells'][sheet]
        for address in sorted(set(before) | set(cells[sheet])):
            if (sheet, address) not in editable and not same_cell(before.get(address), cells[sheet].get(address)):
                changes.append(f'{sheet}!{address}')
    if changes:
        raise ValueError('人工表有尚未支持接续的修改（数量/尺寸/金额/公式/标签/资料等）：'+
                         '、'.join(changes[:20])+'；请保留原表，按ID确认修改并保存台账或恢复原格，不能静默覆盖')

    current = all_items(result)
    confirmed = {r['id']: r for r in result.get('quote_rows', result['rows'])}
    patches, notes, overrides, group_prices = {}, {}, {}, {}
    warnings, invalidated = [], []
    effective = {rid: row.get('unit_price') for rid, row in confirmed.items()}
    old_cells = baseline['baseline_cells']
    group_changes = {}
    for key, old in baseline['groups'].items():
        address = f"D{old['row_number']}"
        cell = cells['报价汇总'].get(address, EMPTY)
        if not same_cell(old_cells['报价汇总'].get(address), cell):
            value = price_value(cell, '汇总'+address)
            group_changes[key] = value
            group_prices[key] = '' if value is None else value
    for rid, old in baseline['rows'].items():
        number = old['row_number']
        row = current.get(rid)
        note_cell = cells['报价明细'].get(f'L{number}', EMPTY)
        if not same_cell(old_cells['报价明细'].get(f'L{number}'), note_cell):
            note = text_value(note_cell, rid+'备注')
            # Generated warnings remain read-only facts; save only user's prose.
            system_note = old.get('system_note', '')
            if system_note and note.startswith(system_note):
                note = note[len(system_note):].lstrip('\n')
            if row is None:
                raise ValueError(rid+'已不在当前台账，但旧表备注有修改；请先恢复/确认该行，不能丢弃备注')
            prior = old.get('remark', '') or ''
            now = row.get('remark', '') or ''
            if now != prior and now != note:
                raise ValueError(rid+'备注并发修改冲突：台账与Excel均已修改，请明确采用哪份')
            if note != now:
                patches.setdefault(rid, {})['remark'] = note
            notes[rid] = note
            if rid not in confirmed:
                warnings.append(rid+'已移入不单列，人工备注保存到台账，未计入报价')
        price_cell = cells['报价明细'].get(f'I{number}', EMPTY)
        price_changed = not same_cell(old_cells['报价明细'].get(f'I{number}'), price_cell)
        group_changed = old['group_id'] in group_changes and bool(old.get('price_formula'))
        if not price_changed and not group_changed:
            continue
        price = price_value(price_cell, rid) if price_changed else group_changes[old['group_id']]
        prior = old.get('price_value')
        prior = None if prior == '' else prior
        # Replacing a link by the same old value alone is not a new quoted price.
        # If D changed too, an explicit I value intentionally overrides that edit.
        if price_changed and price == prior and not group_changed:
            continue
        if row is None:
            raise ValueError(rid+'已不在当前台账，但旧表单价有修改；请先确认该行，不能丢弃价格')
        if row.get('unit') != old['unit'] or (rid in confirmed and group_key(row) != old['group_id']):
            raise ValueError(rid+'计价单位/价格组已经变化，旧表有人工价格修改；请明确新口径价格后重导出')
        now = row.get('unit_price')
        if now != prior and now != price:
            raise ValueError(rid+'单价并发修改冲突：台账与Excel均已修改，请明确采用哪份')
        if price != now:
            patches.setdefault(rid, {}).update(unit_price=price, price_unit=row['unit'] if price is not None else None,
                                               price_note='人工Excel确认单价：'+str(path) if price is not None else '')
        if rid in confirmed:
            effective[rid] = price
            overrides[rid] = '' if price is None else price
        else:
            warnings.append(rid+'已移入不单列，人工单价保存到台账，未计入报价')
    # A classification price edited in an old sheet has no evidence for newly added members.
    for key in group_changes:
        new = [rid for rid, row in confirmed.items() if group_key(row) == key and rid not in baseline['rows']]
        if new:
            warnings.append('旧表分类价仅接续原成员；新增行保留台账价格：'+','.join(new))

    pending = merge_pending(result.get('pending_review_inputs', baseline.get('pending_review_inputs', [])))
    for rid, old in baseline.get('review', {}).items():
        review_sheet=old.get("sheet","待复核"); review_column=old.get("column","H")
        address = f"{review_column}{old['row_number']}"
        cell = cells[review_sheet].get(address, EMPTY)
        if same_cell(old_cells[review_sheet].get(address), cell):
            continue
        text = text_value(cell, rid+'人工确认输入')
        if rid not in current:
            raise ValueError(rid+'复核行已不在台账但人工确认有修改；请先人工对照，不能丢弃')
        origin_key = hashlib.sha256(json.dumps([baseline['workbook_id'],rid,address,text],ensure_ascii=False).encode()).hexdigest()
        if any(p.get('origin_key')==origin_key or (p.get('row_id')==rid and p.get('text')==text and p.get('source')==str(path) and p.get('baseline_revision')==baseline['revision']) for p in pending):
            continue
        if any(p.get('row_id') == rid and p.get('text') == text and p.get('status') == 'pending' for p in pending):
            continue
        latest = next((p for p in reversed(pending) if p.get('row_id') == rid and p.get('status') == 'pending'), None)
        if latest and latest.get('text') != old.get('text', '') and latest.get('text') != text:
            raise ValueError(rid+'人工确认输入并发修改冲突；请核对两份原话后另存确认批次')
        pending.append(dict(id=str(uuid.uuid4()), origin_key=origin_key, row_id=rid, text=text, source=str(path), cell=review_sheet+'!'+address,
                            baseline_revision=baseline['revision'], status='pending', previous_text=old.get('text', '')))
    if any(p.get('status') == 'pending' for p in pending):
        warnings.append('Excel人工确认文本已保存为待处理输入；尚未结构化核验，不自动解除复核状态；可计算项按当前值暂计')
    return dict(source=str(path), overrides=overrides, group_prices=group_prices, notes=notes,
                item_patches=patches, effective_prices=effective, pending_review_inputs=pending,
                warnings=warnings, invalidated=invalidated, identity={k: baseline[k] for k in IDENTITY_KEYS},
                baseline=baseline)
