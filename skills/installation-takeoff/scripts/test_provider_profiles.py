"""Synthetic explicit profile application tests; no user configuration is accessed."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_measurement_v13 import data3, row3
from provider_profiles import preview_profile, apply_profile, save_profile


def profile(kind='rules', **patch):
    value = dict(schema_version=1, kind=kind, profile_id='provider-one', name='服务商口径', version='1',
                 source_text='用户：以后矮柜按实际米数')
    if kind == 'rules':
        value['rules'] = [dict(id='low', selector={'billing_class': 'ordinary_low_cabinet'},
                               method='length', unit='米', minimum=0)]
    else:
        value['source_text'] = '用户：衣柜每平米35元'
        value['prices'] = [dict(id='wardrobe', selector={'product_type': 'cabinet', 'cabinet_subtype': 'ordinary'},
                                method='projection_area', unit='平米', unit_price=35)]
    value.update(patch)
    return value


class ProfileTests(unittest.TestCase):
    def test_preview_is_pure_and_scopes_low_ordinary_cabinets(self):
        project = data3(row3(length_mm=800, height_mm=900), row3(id='Q2', instance_key='C2', height_mm=900,
                       cabinet_subtype='sideboard'), row3(id='Q3', instance_key='C3'))
        original = copy.deepcopy(project)
        report = preview_profile(project, profile())
        self.assertEqual(project, original)
        self.assertEqual([r['item_id'] for r in report['matched']], ['Q1'])
        self.assertEqual(report['unmatched'], [])
        self.assertTrue(report['changes'])

    def test_apply_keeps_other_layers_and_does_not_autosave(self):
        project = data3(row3(length_mm=800, height_mm=900))
        project['rule_overrides'] = [dict(id='project-rule', layer='project', selector={'id': 'Q1'},
            method='length', unit='米', minimum=2, source_text='本项目保底两米')]
        result, changes = apply_profile(project, profile())
        self.assertEqual(len(project['rule_overrides']), 1)
        self.assertEqual(result['rule_overrides'][0], project['rule_overrides'][0])
        self.assertEqual(result['rule_overrides'][1]['layer'], 'provider')
        self.assertTrue(changes)
        self.assertTrue(preview_profile(project, profile())['warnings'])
        from prepare_quote import prepare
        self.assertEqual(prepare(result)['rows'][0]['quantity'], 2)

    def test_reapply_is_idempotent_and_keeps_other_profiles(self):
        project = data3(row3(length_mm=800, height_mm=900))
        result, _ = apply_profile(project, profile())
        again, changes = apply_profile(result, profile())
        self.assertEqual(again, result)
        self.assertEqual(changes, [])
        second = profile(profile_id='another', rules=[dict(id='all', selector={'product_type':'desk'},
                                                         method='length', unit='米')])
        final, _ = apply_profile(result, second)
        self.assertEqual(final['rule_overrides'][:1], result['rule_overrides'])
        self.assertEqual(len(final['provider_profile_bindings']), 2)
        self.assertEqual(apply_profile(final, profile())[0], final)

    def test_rule_profile_update_replaces_only_its_previous_rules(self):
        original, _ = apply_profile(data3(row3(length_mm=800, height_mm=900)), profile())
        changed = profile(version='2', rules=[dict(id='low', selector={'billing_class':'ordinary_low_cabinet'},
                          method='length', unit='米', minimum=.5)])
        result, changes = apply_profile(original, changed)
        self.assertEqual(len(result['rule_overrides']), 1)
        self.assertEqual(result['rule_overrides'][0]['minimum'], .5)
        self.assertTrue(changes)

    def test_duplicate_rule_ids_and_foreign_collisions_rejected(self):
        p = profile()
        p['rules'].append(copy.deepcopy(p['rules'][0]))
        with self.assertRaises(ValueError):
            preview_profile(data3(row3()), p)
        project, _ = apply_profile(data3(row3(length_mm=800, height_mm=900)), profile())
        project.pop('provider_profile_bindings')
        project['rule_overrides'][0]['minimum'] = 3
        with self.assertRaises(ValueError):
            apply_profile(project, profile())

    def test_profile_types_and_metadata_are_validated(self):
        for patch in ({'source_text':''}, {'version':''}, {'profile_id':'../outside'}, {'kind':'other'},
                      {'schema_version':2}, {'prices':[]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                preview_profile(data3(row3()), profile(**patch))

    def test_prices_use_selected_method_unit_and_explicit_subtype(self):
        project = data3(row3(), row3(id='Q2', instance_key='C2', cabinet_subtype='sideboard'))
        result, changes = apply_profile(project, profile('prices'))
        self.assertEqual(result['items'][0]['unit_price'], 35)
        self.assertEqual(result['items'][0]['price_unit'], '平米')
        self.assertEqual(result['items'][0]['price_note'], '用户：衣柜每平米35元')
        self.assertNotIn('unit_price', result['items'][1])
        self.assertNotIn('unit_price', project['items'][0])
        self.assertEqual(len([c for c in changes if c['kind'] == 'price']), 1)

    def test_price_method_unit_mismatch_is_warned_not_applied(self):
        project = data3(row3(length_mm=800, height_mm=900))
        report = preview_profile(project, profile('prices'))
        self.assertEqual(report['matched'], [])
        self.assertEqual(report['unmatched'], ['wardrobe'])
        self.assertTrue(report['warnings'])
        result, _ = apply_profile(project, profile('prices'))
        self.assertNotIn('unit_price', result['items'][0])

    def test_zero_price_valid_negative_nonfinite_and_ambiguous_rejected(self):
        for value in (-1, float('nan'), float('inf'), True, '35'):
            p = profile('prices'); p['prices'][0]['unit_price'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                apply_profile(data3(row3()), p)
        p = profile('prices'); p['prices'][0]['unit_price'] = 0
        self.assertEqual(apply_profile(data3(row3()), p)[0]['items'][0]['unit_price'], 0)
        p['prices'].append(dict(p['prices'][0], id='other', unit_price=20))
        with self.assertRaises(ValueError):
            preview_profile(data3(row3()), p)

    def test_save_is_explicit_atomic_and_only_serializes_supplied_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'profile.json'
            result = save_profile(target, profile())
            self.assertEqual(Path(result), target)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), profile())
            invalid = profile(); invalid['rules'][0]['minimum'] = float('nan')
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                save_profile(target, invalid)
            self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
