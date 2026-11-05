"""Explicit, portable provider profiles. No discovery, autoload, model, or network calls.

preview_profile(takeoff, profile) returns a JSON-ready match/change report without writes.
apply_profile(takeoff, profile) returns (new_takeoff, changes), preserving its inputs.
save_profile(path, profile) writes only when explicitly called and returns the path.
"""
import copy
import json
import os
from pathlib import Path
import re
import tempfile

from prepare_quote import SELECTORS, number, select_rule, validate_rule

IDENTIFIER = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}')


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema_version') != 1 or isinstance(profile.get('schema_version'), bool):
        raise ValueError('profile.schema_version须为1')
    kind = profile.get('kind')
    if kind not in ('rules', 'prices'):
        raise ValueError('profile.kind须为rules或prices，口径与价格分别保存')
    allowed = {'schema_version', 'kind', 'profile_id', 'name', 'version', 'source_text', kind}
    if set(profile) - allowed:
        raise ValueError('profile含未知字段或混合了口径与价格')
    for key in ('profile_id', 'name', 'version', 'source_text'):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError('profile.' + key + '须为非空文本')
    if not IDENTIFIER.fullmatch(profile['profile_id']):
        raise ValueError('profile_id须为可移植的字母/数字/点/横线/下划线标识')
    entries = profile.get(kind)
    if not isinstance(entries, list) or not entries:
        raise ValueError('profile.' + kind + '须为非空数组')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not IDENTIFIER.fullmatch(entry['id']):
            raise ValueError('profile条目需要有效id')
        if entry['id'] in seen:
            raise ValueError('profile条目id重复：' + entry['id'])
        seen.add(entry['id'])
        entry_allowed = {'id', 'selector', 'method', 'unit', 'source_text'} | ({'minimum'} if kind == 'rules' else {'unit_price'})
        if set(entry) - entry_allowed:
            raise ValueError('profile条目含未知字段；规则层固定为provider')
        selector = entry.get('selector')
        if not isinstance(selector, dict) or not selector or set(selector) - (SELECTORS | {'billing_class'}):
            raise ValueError('profile.selector为空或含未知字段')
        if any(not isinstance(value, str) or not value.strip() for value in selector.values()):
            raise ValueError('profile.selector值须为非空文本')
        if 'cabinet_subtype' in selector and selector['cabinet_subtype'] not in {'ordinary', 'sideboard'}:
            raise ValueError('profile.selector.cabinet_subtype无效')
        if 'billing_class' in selector and selector['billing_class'] != 'ordinary_low_cabinet':
            raise ValueError('仅支持billing_class=ordinary_low_cabinet')
        source = entry.get('source_text', profile['source_text'])
        if not isinstance(source, str) or not source.strip():
            raise ValueError('profile条目source_text须保留用户原话')
        # Reuse the calculator's method/unit/minimum contract; billing_class is expanded later.
        valid_selector = {k: v for k, v in selector.items() if k != 'billing_class'} or {'id': 'preview'}
        validate_rule(dict(id=entry['id'], layer='provider', selector=valid_selector,
                           method=entry.get('method'), unit=entry.get('unit'), minimum=entry.get('minimum', 0), source_text=source))
        if kind == 'prices':
            number(entry.get('unit_price'), 'profile.unit_price')
    return profile


def matches(row, selector):
    for key, expected in selector.items():
        if key == 'billing_class':
            if row.get('product_type') != 'cabinet' or row.get('cabinet_subtype') != 'ordinary':
                return False
            try:
                height = number(row.get('height_mm'), 'height_mm', True)
            except ValueError:
                return False
            if not 20 <= height < 1000:
                return False
        elif row.get(key) != expected:
            return False
    return True


def rule_view(rule):
    return {key: rule.get(key, 0 if key == 'minimum' else None) for key in ('id', 'method', 'unit', 'minimum')}


def _prepare_application(takeoff, profile):
    validate_profile(profile)
    if not isinstance(takeoff, dict) or not isinstance(takeoff.get('items'), list):
        raise ValueError('项目items须为数组')
    updated = copy.deepcopy(takeoff)
    items = updated['items']
    if any(not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] for row in items):
        raise ValueError('profile匹配需要稳定行id')
    if len({row['id'] for row in items}) != len(items):
        raise ValueError('项目行id重复')
    overrides = updated.get('rule_overrides', [])
    if not isinstance(overrides, list):
        raise ValueError('rule_overrides须为数组')
    for rule in overrides:
        validate_rule(rule)
    if len({rule['id'] for rule in overrides}) != len(overrides):
        raise ValueError('项目rule_overrides存在重复id')
    bindings = updated.setdefault('provider_profile_bindings', {})
    if not isinstance(bindings, dict):
        raise ValueError('provider_profile_bindings须为对象')
    kind, profile_id = profile['kind'], profile['profile_id']
    binding_key = kind + ':' + profile_id
    previous_binding = bindings.get(binding_key, {})
    if not isinstance(previous_binding, dict):
        raise ValueError('已有profile绑定格式无效')
    report = dict(kind=kind, profile_id=profile_id, version=profile['version'], matched=[], unmatched=[], warnings=[], changes=[])
    candidates = {entry['id']: [row for row in items if not row.get('excluded_reason') and matches(row, entry['selector'])]
                  for entry in profile[kind]}
    new_ids = []
    if kind == 'rules':
        prefix = 'profile:' + profile_id + ':'
        owned = previous_binding.get('rule_ids', [])
        if not isinstance(owned, list) or any(not isinstance(key, str) or not key.startswith(prefix) for key in owned):
            raise ValueError('已有profile规则绑定无效')
        retained = [rule for rule in overrides if not (rule['layer'] == 'provider' and rule['id'] in owned)]
        new_rules = []
        for entry in profile['rules']:
            targets = candidates[entry['id']]
            if not targets:
                report['unmatched'].append(entry['id'])
            # A derived class is a snapshot of known facts. Reapply after adding new entities.
            scopes = [(prefix + entry['id'] + ':' + row['id'], {'id': row['id']}) for row in targets] if 'billing_class' in entry['selector'] else [(prefix + entry['id'], entry['selector'])]
            for rule_id, selector in scopes:
                rule = dict(id=rule_id, layer='provider', selector=copy.deepcopy(selector), method=entry['method'],
                            unit=entry['unit'], minimum=entry.get('minimum', 0), source_text=entry.get('source_text', profile['source_text']))
                collision = next((old for old in retained if old['id'] == rule_id), None)
                if collision is not None and collision != rule:
                    raise ValueError('profile规则id与其他口径冲突：' + rule_id)
                if collision is None:
                    new_rules.append(rule)
                new_ids.append(rule_id)
        final_map = {rule['id']: rule for rule in retained + new_rules}
        final_rules = [final_map[rule['id']] for rule in overrides if rule['id'] in final_map]
        final_rules.extend(rule for rule in new_rules if rule['id'] not in {old['id'] for old in overrides})
        for entry in profile['rules']:
            for row in candidates[entry['id']]:
                try:
                    old, new = select_rule(row, overrides), select_rule(row, final_rules)
                except ValueError as error:
                    report['warnings'].append(row['id'] + '口径待复核：' + str(error))
                    continue
                effective = new['id'] in new_ids
                report['matched'].append(dict(item_id=row['id'], entry_id=entry['id'], effective=effective,
                                              previous=rule_view(old), next=rule_view(new)))
                if not effective:
                    report['warnings'].append(row['id'] + '保留优先级更高或更具体的现有口径：' + new['id'])
        old_map, new_map = {r['id']: r for r in overrides}, {r['id']: r for r in final_rules}
        for rule_id in sorted(set(old_map) | set(new_map)):
            if old_map.get(rule_id) != new_map.get(rule_id):
                report['changes'].append(dict(kind='rule', id=rule_id, old_value=old_map.get(rule_id), new_value=new_map.get(rule_id)))
        updated['rule_overrides'] = final_rules
    else:
        chosen = {}
        for entry in profile['prices']:
            accepted = []
            for row in candidates[entry['id']]:
                try:
                    rule = select_rule(row, overrides)
                except ValueError as error:
                    report['warnings'].append(row['id'] + '口径待复核：' + str(error))
                    continue
                if (rule['method'], rule['unit']) != (entry['method'], entry['unit']):
                    report['warnings'].append(row['id'] + '计量方式/单位与价格模板不符，未套价')
                    continue
                if row['id'] in chosen:
                    raise ValueError('价格模板对同一行存在多个匹配，请缩小selector：' + row['id'])
                chosen[row['id']] = entry['id']
                accepted.append(row)
                previous = {key: row.get(key) for key in ('unit_price', 'price_unit', 'price_note')}
                next_value = dict(unit_price=float(number(entry['unit_price'], '单价')), price_unit=entry['unit'],
                                  price_note=entry.get('source_text', profile['source_text']))
                report['matched'].append(dict(item_id=row['id'], entry_id=entry['id'], previous=previous, next=next_value))
                if previous != next_value:
                    report['changes'].append(dict(kind='price', item_id=row['id'], old_value=previous, new_value=next_value))
                row.update(next_value)
            if not accepted:
                report['unmatched'].append(entry['id'])
    binding = dict(profile_id=profile_id, name=profile['name'], kind=kind, version=profile['version'], source_text=profile['source_text'])
    if kind == 'rules':
        binding['rule_ids'] = new_ids
    if previous_binding != binding:
        report['changes'].append(dict(kind='profile', profile_id=profile_id, profile_kind=kind, old_value=previous_binding or None, new_value=binding))
    bindings[binding_key] = binding
    return updated, report


def preview_profile(takeoff, profile):
    return _prepare_application(takeoff, profile)[1]


def apply_profile(takeoff, profile):
    updated, report = _prepare_application(takeoff, profile)
    return updated, report['changes']


def save_profile(path, profile):
    validate_profile(profile)
    try:
        payload = json.dumps(profile, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    except (ValueError, TypeError) as error:
        raise ValueError('profile无法序列化：' + str(error)) from None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path
