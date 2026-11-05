"""Quote all observed items, with unresolved quantities explicitly provisional.

Evidence verdicts remain separate: provisional inclusion is not confirmation.
"""
import copy
import re
from collections import Counter
from decimal import Decimal


def pricing_scope(row):
    if row.get('price_scope'):
        return row['price_scope']
    name = row.get('name', '')
    # A neighbouring pants drawer in a location description is not the item type.
    if row.get('product_type') == 'drawer' and re.search(r'裤抽(?!上方|下方|旁|左侧|右侧)', name):
        return '裤抽'
    return '默认'


def quotation_view(result):
    from prepare_quote import select_rule, inherit_parent_repeat, measure, number, rounded
    originals = result.get('all_items', [])
    by_id = {r['id']: r for r in originals}
    duplicate = Counter((r.get('instance_key'), r.get('product_type')) for r in originals
                        if not r.get('excluded_reason') and isinstance(r.get('instance_key'), str)
                        and isinstance(r.get('product_type'), str))
    rows = {r['id']: copy.deepcopy(r) for r in result['rows']}
    for original in result['review']:
        row = copy.deepcopy(original)
        row.update(provisional=True, quantity=None, amount=None, unit_price=None,
                   status='待复核·未计量', formula='', price_issue='')
        try:
            if not isinstance(row.get('instance_key'), str) or duplicate[(row['instance_key'], row.get('product_type'))] > 1:
                raise ValueError('实体缺失或重复，先核对归属后计量')
            item = inherit_parent_repeat(original, by_id)
            rule = select_rule(item, result['rule_overrides'])
            row.update(method=rule['method'], unit=rule['unit'], rule_id=rule['id'])
            qty, measured, formula = measure(item, rule)
            if qty <= 0:
                raise ValueError('当前尺寸舍入后无有效数量')
            row.update(quantity=qty, measured_quantity=measured, formula=formula,
                       status='待复核·已暂计')
        except (ValueError, TypeError, KeyError) as error:
            row['calculation_issue'] = str(error)
        if original.get('unit_price') is not None:
            try:
                if not row.get('unit') or original.get('price_unit') != row['unit'] or not original.get('price_note'):
                    raise ValueError('价格单位变化或缺少用户报价依据，请重新补价')
                row['unit_price'] = float(number(original['unit_price'], '单价'))
                if row['quantity'] is not None:
                    row['amount'] = rounded(Decimal(str(row['quantity'])) * Decimal(str(row['unit_price'])), 2)
            except ValueError as error:
                row['price_issue'] = str(error)
        for field in ('product_type','cabinet_subtype','name','space'):
            if row.get(field) is not None and not isinstance(row[field], str):
                row[field] = '未明确'
        rows[row['id']] = row
    order = {r['id']: i for i, r in enumerate(originals)}
    quoted = sorted(rows.values(), key=lambda r: order.get(r['id'], len(order)))
    groups = {}
    for row in quoted:
        scope = pricing_scope(row)
        key = (row['product_type'], row.get('method'), row.get('unit'), row.get('unit_price'), row.get('cabinet_subtype'), scope, row.get('rule_id'))
        group = groups.setdefault(key, dict(product_type=key[0], method=key[1], unit=key[2], unit_price=key[3],
            cabinet_subtype=key[4], price_scope=scope, rule_id=row.get('rule_id'), quantity=None, amount=None, provisional_quantity=0., review_count=0, unmeasured_count=0))
        if row.get('provisional'): group['review_count'] += 1
        if row.get('quantity') is None:
            group['unmeasured_count'] += 1
        else:
            group['quantity'] = rounded(Decimal(str(group['quantity'] or 0)) + Decimal(str(row['quantity'])))
            if row.get('provisional'):
                group['provisional_quantity'] = rounded(Decimal(str(group['provisional_quantity'])) + Decimal(str(row['quantity'])))
        if row.get('amount') is not None:
            group['amount'] = rounded(Decimal(str(group['amount'] or 0)) + Decimal(str(row['amount'])), 2)
    return dict(quote_rows=quoted, quote_summary=list(groups.values()),
                quote_unmeasured_count=sum(r.get('quantity') is None for r in quoted))


def review_note(row):
    if not row.get('provisional'):
        return ''
    text = '待复核：' + str(row.get('reason') or row.get('review_reason') or '请核对图纸')
    if row.get('quantity') is not None:
        return text + '；按本行所列尺寸/数量暂计，已纳入汇总。'
    return text + '；未计量：' + str(row.get('calculation_issue', '缺少必要数据'))
