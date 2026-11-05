"""Evidence consistency checks, not a guarantee that a drawing was read correctly."""
from decimal import Decimal
import re

ACCESSORIES = {'light_strip', 'drawer', 'pull_basket', 'dish_basket', 'seasoning_basket'}
CABINETS = {'cabinet', 'wall_cabinet', 'base_cabinet'}
DIMENSIONS = {'length_mm', 'height_mm', 'depth_mm'}
TOLERANCE = Decimal('0.5')
PATH = re.compile(r'(installation_lengths_mm)\[(\d+)\]|components\[(\d+)\]\.(length_mm|width_mm|count)')


def objects(value, label):
    if not isinstance(value, list):
        raise ValueError(f'{label}须为数组')
    for index, child in enumerate(value):
        if not isinstance(child, dict):
            raise ValueError(f'{label}[{index}]须为对象')
    return value


def valid_field(field):
    return isinstance(field, str) and (field in DIMENSIONS | {'count', 'repeat_count', 'boundary', 'all',
        'side_panel_area', 'manual_quantity', 'accessories', 'classification'} or PATH.fullmatch(field))


def field_value(item, field):
    match = PATH.fullmatch(field) if isinstance(field, str) else None
    if not match:
        return item.get(field)
    if match[1]:
        values, index = item.get('installation_lengths_mm', []), int(match[2])
        return values[index] if isinstance(values, list) and index < len(values) else None
    values, index = item.get('components', []), int(match[3])
    return values[index].get(match[4]) if isinstance(values, list) and index < len(values) and isinstance(values[index], dict) else None


def consumed_fields(item, rule):
    """Only facts used by the selected rule block it; accessory count is always used."""
    method, fields = rule['method'], {'repeat_count', 'classification'}
    if method == 'projection_area':
        fields |= {'length_mm', 'height_mm', 'boundary'}
        if item.get('side_panel_area'):
            fields.add('side_panel_area')
    elif method == 'plan_area':
        fields |= {'length_mm', 'depth_mm', 'boundary'}
    elif method == 'length':
        fields.add('boundary')
        if item.get('product_type') in ACCESSORIES:
            lengths = item.get('installation_lengths_mm', [])
            if not isinstance(lengths, list):
                raise ValueError('installation_lengths_mm须为数组')
            fields |= {f'installation_lengths_mm[{i}]' for i in range(len(lengths))}
        else:
            fields.add('length_mm')
        if rule.get('id') == 'quote-app-36791a0' and item.get('product_type') == 'cabinet':
            fields.add('height_mm')
    elif method == 'developed_area':
        fields.add('boundary')
        for i, _ in enumerate(objects(item.get('components', []), 'components')):
            fields |= {f'components[{i}].{key}' for key in ('length_mm', 'width_mm', 'count')}
    elif method == 'count':
        fields.add('count')
    elif method == 'manual':
        fields.add('manual_quantity')
    if item.get('product_type') in ACCESSORIES:
        fields.add('count')
    return fields


def dimension_fields(fields):
    return {field for field in fields if field in DIMENSIONS or field.startswith('installation_lengths_mm[')
            or (field.startswith('components[') and not field.endswith('.count'))}


def source_for(item, index, label):
    sources = item.get('sources', [])
    if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(sources):
        raise ValueError(f'{label}.source_index无效')
    source = sources[index]
    if not isinstance(source, dict) or source.get('role', 'measurement') != 'measurement':
        raise ValueError(f'{label}不能使用context来源作为计量依据')
    return source


def note_issues(item, evidence):
    """Supplement only: written known uncertainty must not disappear behind empty arrays."""
    issues = []
    for note in [evidence.get('note', ''), item.get('remark', ''), item.get('review_reason', '')]:
        if not isinstance(note, str):
            continue
        for phrase in re.split(r'[；;。\n]', note):
            cleaned = re.sub(r'(?:未发现|没有|无)(?:尺寸链)?(?:冲突|矛盾|疑点)|(?:no|without)\s+(?:conflict|ambiguity)', '', phrase, flags=re.I)
            if not re.search(r'不清|未确认|待复核|待确认|不确定|疑似|可能|冲突|矛盾|uncertain|unresolved|unclear|conflict|ambiguous', cleaned, re.I):
                continue
            fields = [field for field, words in (
                ('length_mm', r'长度|总宽|宽度|水平|length'), ('height_mm', r'高度|总高|height'),
                ('depth_mm', r'深度|depth'), ('count', r'数量|计数|安装位置|count'),
                ('repeat_count', r'重复|套数|repeat'), ('boundary', r'边界|封板|投影|转角'))
                if re.search(words, phrase, re.I)]
            issues.extend(dict(field=field, reason='文字记录仍含疑点：' + phrase) for field in fields or ['all'])
    return issues


def check_evidence(item, rule, number, policy=2):
    e = item.get('evidence')
    if not isinstance(e, dict) or not isinstance(e.get('note'), str) or not e['note'].strip():
        raise ValueError('缺少结构化证据 evidence，请核对外轮廓或数量标注')
    basis = e.get('basis')
    if not isinstance(basis, str) or basis not in {'explicit_dimensions', 'explicit_count', 'user_confirmed'}:
        raise ValueError('仅视觉候选或证据类型不明确，需标注/用户确认')
    user_basis = basis == 'user_confirmed'
    confirmed = user_basis and policy < 3  # Legacy blanket declarations do not upgrade to v3.
    if user_basis and (not isinstance(item.get('user_confirmation'), str) or not item['user_confirmation'].strip()):
        raise ValueError('人工证据缺少用户确认原话')
    active = consumed_fields(item, rule)
    dimensions = dimension_fields(active)
    warnings, issues, resolved = [], [], set()
    for key in ('dimension_checks', 'adjustments', 'ambiguities'):
        if not isinstance(e.get(key), list):
            raise ValueError(f'evidence.{key}须为数组，未发现填[]，不能省略检查')
    for key in ('dimension_checks', 'adjustments', 'count_marks', 'dimension_readings', 'chain_check', 'unresolved_issues', 'source_checks'):
        if key in e:
            objects(e[key], 'evidence.' + key)
    confirmations = {}
    if policy >= 3:
        for index, confirmation in enumerate(objects(e.get('field_confirmations', []), 'evidence.field_confirmations')):
            field = confirmation.get('field')
            label = f'evidence.field_confirmations[{index}]'
            if not valid_field(field) or field in {'all', 'classification', 'accessories'} or field in confirmations:
                raise ValueError(label + '.field无效或重复；不得整行确认')
            if any(not isinstance(confirmation.get(k), str) or not confirmation[k].strip() for k in ('reason', 'user_confirmation')):
                raise ValueError(label + '缺少reason或用户原话user_confirmation')
            if field == 'boundary':
                if confirmation.get('value') != 'clear' or e.get('boundary') != 'clear':
                    raise ValueError(label + '须确认当前boundary=clear，并保留原有疑点')
            else:
                value = number(confirmation.get('value'), label + '.value', True)
                actual = number(field_value(item, field), field, True)
                if value != actual:
                    raise ValueError(label + '.value与当前取量字段值不一致')
                if (field in {'count', 'repeat_count'} or field.endswith('.count')) and value % 1:
                    raise ValueError(label + '计数确认须为整数')
            confirmations[field] = confirmation
        if user_basis and not confirmations:
            raise ValueError('v3 user_confirmed须有逐字段field_confirmations；整行原话不能代替各字段证据')
    if not isinstance(e.get('boundary'), str) or e['boundary'] not in {'clear', 'not_applicable', 'uncertain'}:
        raise ValueError('evidence.boundary无效')
    if 'boundary' in active and e.get('boundary') != 'clear' and not confirmed:
        raise ValueError('主体边界、封板或相邻投影尚未明确')
    if dimensions - confirmations.keys() and not confirmed and basis != 'explicit_dimensions':
        if not ((policy >= 3 and user_basis) or (item.get('product_type') in ACCESSORIES and basis == 'explicit_count' and e.get('dimension_readings'))):
            raise ValueError('尺寸计量需要dimension_readings及明确主体边界')

    def add_issue(field, reason):
        if not valid_field(field) or not isinstance(reason, str) or not reason.strip():
            raise ValueError('未解决疑点须有有效field与reason')
        issues.append(dict(field=field, reason=reason))

    for ambiguity in e['ambiguities']:
        if isinstance(ambiguity, str) and ambiguity.strip():
            add_issue('all', ambiguity)
        elif isinstance(ambiguity, dict):
            add_issue(ambiguity.get('field'), ambiguity.get('reason'))
        else:
            raise ValueError('evidence.ambiguities元素须为非空文本或field/reason对象')
    for issue in e.get('unresolved_issues', []):
        add_issue(issue.get('field'), issue.get('reason'))
    for check in e.get('source_checks', []):
        if not valid_field(check.get('field')) or not isinstance(check.get('status'), str) or check['status'] not in {'matched', 'conflict', 'unavailable'}:
            raise ValueError('evidence.source_checks字段或状态无效')
        if check['status'] == 'conflict':
            add_issue(check['field'], check.get('reason'))

    adjustments = {}
    for index, adjustment in enumerate(e['adjustments']):
        field = adjustment.get('field')
        if not valid_field(field) or not isinstance(adjustment.get('evidence'), str) or not adjustment['evidence'].strip():
            raise ValueError(f'evidence.adjustments[{index}]缺少有效field或图纸依据')
        if field in adjustments:
            raise ValueError(f'{field}存在重复扣减记录')
        original = number(adjustment.get('original_mm'), field + '扣减前尺寸', True)
        value = original - number(adjustment.get('deduction_mm'), field + '扣减尺寸')
        selected = number(field_value(item, field), field, True)
        if value <= 0 or abs(value - selected) > TOLERANCE:
            add_issue(field, '扣减后的尺寸与取量尺寸不一致')
        adjustments[field] = adjustment

    def check_chain(chain, label):
        field = chain.get('field')
        if not valid_field(field):
            raise ValueError(label + '.field无效')
        overall = number(chain.get('overall_mm'), field + '尺寸链总长', True)
        parts = chain.get('segments_mm')
        if not isinstance(parts, list) or not parts:
            raise ValueError(label + '.segments_mm须有分段数值')
        total = sum((number(x, field + '尺寸链分段', True) for x in parts), Decimal(0))
        selected = number(field_value(item, field), field, True)
        if abs(overall - total) > TOLERANCE:
            resolution = chain.get('resolution', {})
            if not isinstance(resolution, dict):
                raise ValueError(label + '.resolution须为对象')
            if all(isinstance(resolution.get(k), str) and resolution[k].strip() for k in ('user_confirmation', 'reason')) and number(resolution.get('selected_mm'), field + '确认尺寸', True) == selected:
                resolved.add(field)
            else:
                add_issue(field, f'{field}尺寸链冲突：总标{overall} / 分段和{total}，需记录确认口径')
        elif abs(selected - overall) > TOLERANCE:
            adjustment = adjustments.get(field)
            if not adjustment or abs(number(adjustment['original_mm'], field, True) - overall) > TOLERANCE:
                add_issue(field, f'{field}取量尺寸与已核对尺寸链不一致，需保留扣减依据')

    for index, chain in enumerate(e['dimension_checks']):
        check_chain(chain, f'evidence.dimension_checks[{index}]')
    if policy >= 3:
        readings = objects(e.get('dimension_readings'), 'evidence.dimension_readings')
        chains = objects(e.get('chain_check'), 'evidence.chain_check')
        read_fields, checked_fields = set(), set()
        for index, reading in enumerate(readings):
            field, label = reading.get('field'), f'evidence.dimension_readings[{index}]'
            if not valid_field(field) or any(not isinstance(reading.get(k), str) or not reading[k].strip() for k in ('text', 'region')):
                raise ValueError(label + '缺少有效field/text/region')
            if field in read_fields:
                raise ValueError(label + '同一field重复读数；保留冲突到chain_check或unresolved_issues')
            read_fields.add(field)
            source_for(item, reading.get('source_index'), label)
            actual = number(reading.get('value_mm'), label + '.value_mm', True)
            if 'box' in reading:
                box = reading['box']
                if not isinstance(box, list) or len(box) != 4:
                    raise ValueError(label + '.box须为归一化四元组')
                values = [number(v, label + '.box') for v in box]
                if max(values) > 1 or values[0] >= values[2] or values[1] >= values[3]:
                    raise ValueError(label + '.box坐标无效')
            expected = adjustments[field]['original_mm'] if field in adjustments else field_value(item, field)
            if abs(actual - number(expected, field, True)) > TOLERANCE:
                add_issue(field, f'{field}原始读数与取量尺寸不一致')
        for index, chain in enumerate(chains):
            field, status = chain.get('field'), chain.get('status')
            if not valid_field(field) or field in checked_fields or not isinstance(status, str):
                raise ValueError(f'evidence.chain_check[{index}].field无效或重复')
            checked_fields.add(field)
            if status == 'checked':
                check_chain(chain, f'evidence.chain_check[{index}]')
            elif status in {'not_present', 'unresolved'}:
                if not isinstance(chain.get('reason'), str) or not chain['reason'].strip():
                    raise ValueError(f'evidence.chain_check[{index}]须说明reason')
                if status == 'unresolved':
                    add_issue(field, chain['reason'])
                if chain.get('segments_mm') or chain.get('overall_mm') is not None:
                    raise ValueError(f'evidence.chain_check[{index}]有尺寸链数据，不能声明not_present/unresolved代替核对')
            else:
                raise ValueError(f'evidence.chain_check[{index}].status无效')
        if not confirmed:
            missing_readings = dimensions - read_fields - confirmations.keys()
            missing_chains = dimensions - checked_fields - confirmations.keys()
            if missing_readings:
                raise ValueError('dimension_readings缺少取量字段：' + '、'.join(sorted(missing_readings)))
            if missing_chains:
                raise ValueError('chain_check缺少尺寸链检查：' + '、'.join(sorted(missing_chains)))
        if rule['method'] == 'manual' and 'manual_quantity' not in confirmations:
            raise ValueError('manual_quantity需要逐字段field_confirmations及人工计算依据')
        issues.extend(issue for issue in note_issues(item, e) if issue['field'] not in resolved)
        for field in DIMENSIONS - active:
            if item.get(field) is not None:
                try:
                    value = number(item[field], field, True)
                    if value < 20 or value > 30000:
                        raise ValueError(field + '异常，请核对毫米单位/尺寸轴')
                except ValueError as error:
                    add_issue(field, str(error))
    if 'count' in active and not confirmed:
        marks = e.get('count_marks', [])
        if not marks and 'count' not in confirmations:
            raise ValueError('count缺少逐处count_marks明确标注，外观抽格仅列候选')
        total = Decimal(0)
        for index, mark in enumerate(marks):
            quantity = number(mark.get('quantity'), '单处标注数量', True)
            if quantity % 1 or any(not isinstance(mark.get(k), str) or not mark[k].strip() for k in ('label', 'region')):
                raise ValueError(f'count_marks[{index}]须有原文、区域和整数数量')
            if policy >= 3:
                source_for(item, mark.get('source_index'), f'count_marks[{index}]')
            total += quantity
        if marks and total != number(item.get('count'), 'count计数', True):
            add_issue('count', f'逐处标注合计{total}与count不一致')

    def confirmation_warning(field, reason):
        original = {}
        for key in ('dimension_readings', 'dimension_checks', 'chain_check', 'adjustments', 'source_checks', 'unresolved_issues'):
            entries = [entry for entry in e.get(key, []) if entry.get('field') == field]
            if entries:
                original[key] = entries
        if field == 'count' and e.get('count_marks'):
            original['count_marks'] = e['count_marks']
        confirmation = confirmations[field]
        return dict(field=field, reason=reason, resolution='user_confirmed', confirmed_value=confirmation['value'],
                    original_values=original, confirmation_reason=confirmation['reason'], user_confirmation=confirmation['user_confirmation'])

    # Even a confirmation that fills a missing reading remains visible as an explicit user fact.
    warnings.extend(confirmation_warning(field, '按逐字段用户确认取量') for field in confirmations)
    for issue in issues:
        if issue['field'] in confirmations:
            warnings.append(confirmation_warning(issue['field'], issue['reason']))
            continue
        if issue['field'] == 'all' or issue['field'] in active or policy < 3:
            raise ValueError(issue['field'] + '：' + issue['reason'])
        if issue not in warnings:
            warnings.append(issue)
    return warnings


def check_coverage(page_map, items, policy):
    """Inventory consistency is separate from page registration and measurement truth."""
    if policy < 3:
        return dict(product_coverage_complete=False, accessory_coverage_complete=False,
                    coverage_issues=['旧版证据策略未提供v3产品视图与配件覆盖记录'])
    by_id = {row['id']: row for row in items}
    product_issues, accessory_issues, linked = [], [], set()
    for (file, page_number), page in page_map.items():
        if page['page_type'] != 'product_drawing':
            continue
        label = f'{file}第{page_number}页'
        try:
            views = objects(page.get('views'), label + '.views')
            if not views:
                raise ValueError(label + '缺少产品视图去向')
            view_ids = set()
            for view in views:
                view_id, disposition = view.get('view_id'), view.get('disposition')
                if not isinstance(view_id, str) or not view_id or view_id in view_ids:
                    raise ValueError(label + '视图编号为空或重复')
                view_ids.add(view_id)
                ids = view.get('item_ids', [])
                if not isinstance(ids, list) or any(not isinstance(i, str) or i not in by_id for i in ids):
                    raise ValueError(label + '/' + view_id + '关联item_ids无效')
                if not isinstance(disposition, str) or disposition not in {'measured', 'duplicate', 'reference', 'non_billable', 'unresolved'} or not view.get('reason'):
                    raise ValueError(label + '/' + view_id + '去向或原因缺失')
                if disposition == 'unresolved':
                    raise ValueError(label + '/' + view_id + '视图尚未处理：' + str(view['reason']))
                if disposition in {'measured', 'duplicate'}:
                    if not ids:
                        raise ValueError(label + '/' + view_id + '缺少关联实体')
                    for row_id in ids:
                        sources = by_id[row_id].get('sources', [])
                        if not isinstance(sources, list) or not any(isinstance(s, dict) and s.get('file') == file and s.get('page') == page_number for s in sources):
                            raise ValueError(label + '/' + view_id + '关联实体缺少该视图来源')
                    linked.update(ids)
        except ValueError as error:
            product_issues.append(str(error))
    for row in items:
        if not row.get('excluded_reason') and row['id'] not in linked and row.get('product_type') != 'custom':
            product_issues.append(row['id'] + '未关联已处理产品视图')
        if not isinstance(row.get('product_type'), str) or row['product_type'] not in CABINETS:
            continue
        try:
            checks = row.get('accessory_checks')
            if not isinstance(checks, dict):
                raise ValueError('缺少accessory_checks对象')
            checked, present, unresolved = checks.get('checked_types'), checks.get('present', {}), checks.get('unresolved', {})
            if not isinstance(checked, list) or any(not isinstance(k, str) for k in checked) or set(checked) != ACCESSORIES or len(checked) != len(ACCESSORIES):
                raise ValueError('accessory_checks.checked_types须覆盖五类配件且不重复')
            if not isinstance(present, dict) or not isinstance(unresolved, dict) or (set(present) | set(unresolved)) - ACCESSORIES or not checks.get('note'):
                raise ValueError('accessory_checks.present/unresolved/note无效')
            if unresolved:
                raise ValueError('配件仍有候选/疑点：' + '、'.join(unresolved))
            children = {child['id']: child for child in items if child.get('parent_id') == row['id'] and isinstance(child.get('product_type'), str) and child['product_type'] in ACCESSORIES}
            declared = set()
            for kind, ids in present.items():
                if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in children or children[i]['product_type'] != kind for i in ids):
                    raise ValueError('accessory_checks.present未关联有效配件子行')
                if len(set(ids)) != len(ids) or declared & set(ids):
                    raise ValueError('accessory_checks.present配件编号重复')
                declared.update(ids)
            if declared != set(children):
                raise ValueError('配件子行与accessory_checks.present不一致')
            if note_issues({}, dict(note=checks['note'])):
                raise ValueError('配件检查note仍含未解决疑点')
        except ValueError as error:
            accessory_issues.append(row['id'] + '：' + str(error))
    return dict(product_coverage_complete=not product_issues, accessory_coverage_complete=not accessory_issues,
                coverage_issues=product_issues + accessory_issues)
