"""Pure, synthetic v1.3 measurement/evidence regressions; no real drawings."""
import copy
import unittest

from prepare_quote import prepare, select_rule
from test_prepare_quote import data, item


ACCESSORIES = ['drawer', 'light_strip', 'pull_basket', 'dish_basket', 'seasoning_basket']


def proof(**patch):
    value = dict(basis='explicit_dimensions', boundary='clear', note='原图外轮廓标注',
                 dimension_checks=[], adjustments=[], ambiguities=[])
    value.update(patch)
    return value


def evidence3(row, fields=None, **patch):
    fields = fields if fields is not None else ['length_mm', 'height_mm']
    readings = []
    for field in fields:
        if field.startswith('installation_lengths_mm['):
            value = row['installation_lengths_mm'][int(field.split('[')[1][:-1])]
        else:
            value = row[field]
        readings.append(dict(field=field, value_mm=value, source_index=0,
                             text=str(value), region='外轮廓尺寸线'))
    value = proof(dimension_readings=readings,
                  chain_check=[dict(field=f, status='not_present', reason='本视图仅标总尺寸') for f in fields],
                  unresolved_issues=[])
    value.update(patch)
    return value


def row3(**patch):
    row = item(cabinet_subtype='ordinary', accessory_checks=dict(
        checked_types=ACCESSORIES[:], present={}, unresolved={}, note='其余未发现标注或候选'))
    row.update(patch)
    row.setdefault('evidence', evidence3(row))
    return row


def data3(*rows, **patch):
    value = data(*rows, evidence_policy=3, pages=[dict(file='synthetic.pdf', page=1,
                 page_type='product_drawing', views=[dict(view_id='A', disposition='measured',
                 item_ids=[r['id'] for r in rows], reason='立面与配件标注')])])
    value.update(patch)
    return value


def child(**patch):
    row = item(id='A1', instance_key='C1-drawer', parent_id='Q1', product_type='drawer',
               name='抽屉', count=3, evidence=proof(basis='explicit_count', boundary='not_applicable',
               count_marks=[dict(quantity=3, label='抽屉×3', region='柜内下部')]))
    row.update(patch)
    return row


LENGTH_RULE = dict(id='light-length', layer='project', selector=dict(product_type='light_strip'),
                   method='length', unit='米', source_text='本单灯带按米')


class RepeatAndLengthTests(unittest.TestCase):
    def test_inherit_repeat_exactly_once_without_mutating_observation(self):
        parent, accessory = item(repeat_count=2, repeat_evidence='衣柜×2'), child()
        source = data(parent, accessory, evidence_policy=1)
        result = prepare(source)
        self.assertEqual(result['rows'][1]['quantity'], 6)
        self.assertEqual(result['rows'][1]['repeat_count'], 2)
        self.assertNotIn('repeat_count', accessory)
        self.assertEqual(prepare(source)['rows'][1]['quantity'], 6)

    def test_equal_explicit_repeat_not_multiplied_twice(self):
        result = prepare(data(item(repeat_count=2, repeat_evidence='衣柜×2'),
                              child(repeat_count=2, repeat_evidence='两套柜'), evidence_policy=1))
        self.assertEqual(result['rows'][1]['quantity'], 6)

    def test_inconsistent_child_repeat_is_local_review(self):
        for count in (1, 3):
            result = prepare(data(item(repeat_count=2, repeat_evidence='衣柜×2'),
                                  child(repeat_count=count, repeat_evidence='配件倍数'), evidence_policy=1))
            self.assertEqual([r['id'] for r in result['rows']], ['Q1'])
            self.assertIn('repeat_count', result['review'][0]['reason'])

    def test_one_accessory_uses_same_lengths_source_as_many(self):
        for count in (1, 6):
            light = child(product_type='light_strip', name='灯带', count=count,
                          length_mm=2400, installation_lengths_mm=[500] * count)
            result = prepare(data(item(), light, rule_overrides=[LENGTH_RULE]))
            self.assertEqual(result['rows'][1]['quantity'], count * .5)

    def test_one_accessory_requires_installation_lengths(self):
        light = child(product_type='light_strip', name='灯带', count=1, length_mm=2400)
        result = prepare(data(item(), light, rule_overrides=[LENGTH_RULE]))
        self.assertEqual([r['id'] for r in result['review']], ['A1'])

    def test_length_override_does_not_bypass_count_marks(self):
        light = child(product_type='light_strip', name='灯带', count=6,
                      installation_lengths_mm=[500] * 6, evidence=proof())
        result = prepare(data(item(evidence=proof()), light, evidence_policy=2, rule_overrides=[LENGTH_RULE]))
        self.assertEqual([r['id'] for r in result['review']], ['A1'])
        self.assertIn('count', result['review'][0]['reason'])

    def test_length_override_accepts_count_basis_with_dimension_readings(self):
        light = child(product_type='light_strip', name='灯带', count=1, installation_lengths_mm=[500])
        light['evidence'] = evidence3(light, ['installation_lengths_mm[0]'], basis='explicit_count',
            count_marks=[dict(quantity=1, label='加灯', region='层板下', source_index=0)])
        parent = row3()
        parent['accessory_checks']['present'] = {'light_strip': ['A1']}
        result = prepare(data3(parent, light, rule_overrides=[LENGTH_RULE]))
        self.assertEqual(result['review'], [])
        self.assertEqual(result['rows'][1]['quantity'], .5)

    def test_accessory_lengths_apply_range_and_integer_count_checks(self):
        for patch in ({'installation_lengths_mm': [1]}, {'installation_lengths_mm': [50000]},
                      {'count': 1.5, 'installation_lengths_mm': [500]}, {'count': True, 'installation_lengths_mm': [500]}):
            light = child(product_type='light_strip', name='灯带', count=1, installation_lengths_mm=[500])
            light.update(patch)
            self.assertEqual(len(prepare(data(item(), light, rule_overrides=[LENGTH_RULE]))['review']), 1)


class V3EvidenceTests(unittest.TestCase):
    def test_valid_v3_evidence_and_zero_price_complete(self):
        result = prepare(data3(row3(unit_price=0, price_unit='平米', price_note='用户：免费')))
        self.assertFalse(result['review'])
        self.assertEqual(result['complete_total'], 0)
        self.assertTrue(result['page_coverage_complete'])
        self.assertTrue(result['product_coverage_complete'])
        self.assertTrue(result['accessory_coverage_complete'])

    def test_subtype_not_display_name_drives_rule(self):
        for name in ('餐边柜', '客餐厅组合', '矮柜'):
            row = row3(name=name, cabinet_subtype='sideboard', height_mm=900)
            result = prepare(data3(row))
            self.assertEqual(result['rows'][0]['quantity'], 2.16)
            self.assertEqual(result['rows'][0]['unit'], '平米')
        row = row3(name='餐边柜', cabinet_subtype='ordinary', height_mm=900)
        self.assertEqual(prepare(data3(row))['rows'][0]['unit'], '米')

    def test_missing_or_inconsistent_classification_is_review(self):
        for patch in ({'cabinet_subtype': None}, {'cabinet_subtype': 'other'}, {'name': '厨房吊柜'}):
            result = prepare(data3(row3(**patch)))
            self.assertEqual(len(result['review']), 1)

    def test_subtype_rule_selector(self):
        rule = dict(id='sideboard-length', layer='project', selector={'cabinet_subtype': 'sideboard'},
                    method='length', unit='米', source_text='餐边柜按米')
        result = prepare(data3(row3(cabinet_subtype='sideboard'), rule_overrides=[rule]))
        self.assertEqual(result['rows'][0]['method'], 'length')

    def test_empty_reading_or_chain_arrays_cannot_pass(self):
        for field in ('dimension_readings', 'chain_check'):
            row = row3()
            row['evidence'][field] = []
            self.assertEqual(len(prepare(data3(row))['review']), 1)

    def test_reading_must_match_selected_value_and_measurement_source(self):
        for patch in ({'value_mm': 2300}, {'source_index': 7}, {'source_index': True}, {'box': [0, 0, 2, 1]}):
            row = row3()
            row['evidence']['dimension_readings'][0].update(patch)
            self.assertEqual(len(prepare(data3(row))['review']), 1)

    def test_context_source_allowed_but_not_used_as_measurement(self):
        row = row3()
        row['sources'].append(dict(file='synthetic.pdf', page=2, region='厨房布局', text='用于定位', role='context'))
        project = data3(row)
        project['documents'][0]['page_count'] = 2
        project['pages'].append(dict(file='synthetic.pdf', page=2, page_type='spatial_plan'))
        self.assertFalse(prepare(project)['review'])
        row['evidence']['dimension_readings'][0]['source_index'] = 1
        self.assertEqual(len(prepare(project)['review']), 1)

    def test_known_uncertainty_in_note_not_hidden_by_empty_arrays(self):
        row = row3()
        row['evidence']['note'] = '总高有冲突，尚未确认封板是否计入'
        self.assertEqual(len(prepare(data3(row))['review']), 1)

    def test_unconsumed_dimension_issue_is_warning_and_blanks_display_value(self):
        row = row3()
        row['evidence']['unresolved_issues'] = [dict(field='depth_mm', reason='深度标注不清')]
        result = prepare(data3(row))
        self.assertFalse(result['review'])
        self.assertIsNone(result['rows'][0]['depth_mm'])
        self.assertEqual(result['rows'][0]['unverified_fields']['depth_mm'], 600)
        self.assertTrue(result['rows'][0]['evidence_warnings'])
        row['evidence']['unresolved_issues'][0]['field'] = 'height_mm'
        self.assertEqual(len(prepare(data3(row))['review']), 1)

    def test_native_source_conflict_is_field_scoped(self):
        row = row3()
        row['evidence']['source_checks'] = [dict(field='depth_mm', status='conflict', reason='文字层为650')]
        result = prepare(data3(row))
        self.assertFalse(result['review'])
        self.assertIsNone(result['rows'][0]['depth_mm'])
        row['evidence']['source_checks'][0]['field'] = 'height_mm'
        self.assertEqual(len(prepare(data3(row))['review']), 1)

    def test_chain_mismatch_requires_specific_resolution(self):
        row = row3()
        row['evidence']['chain_check'][0] = dict(field='length_mm', status='checked', overall_mm=2400,
                                                segments_mm=[1200, 1198])
        self.assertEqual(len(prepare(data3(row))['review']), 1)
        row['evidence']['chain_check'][0]['resolution'] = dict(selected_mm=2400, reason='用户确认采用总宽',
                                                            user_confirmation='用户：总宽按2400')
        self.assertFalse(prepare(data3(row))['review'])


class LocalizedAndCoverageTests(unittest.TestCase):
    def test_malformed_nested_rows_localize_review(self):
        patches = [dict(sources=[None]), dict(sources={}), dict(evidence=proof(dimension_checks=[None])),
                   dict(evidence=proof(adjustments=[None])), dict(evidence=proof(count_marks=[None])),
                   dict(linear_segments=[None]), dict(components=[None])]
        for patch in patches:
            with self.subTest(patch=patch):
                bad = item(id='Q2', instance_key='C2', evidence=proof())
                bad.update(patch)
                result = prepare(data(item(evidence=proof()), bad, evidence_policy=2))
                self.assertEqual([r['id'] for r in result['rows']], ['Q1'])
                self.assertEqual([r['id'] for r in result['review']], ['Q2'])

    def test_malformed_nested_enum_and_source_keys_are_local(self):
        patches = [dict(product_type=[]), dict(instance_key=[]), dict(name=[]),
                   dict(evidence=proof(basis=[])), dict(evidence=proof(boundary=[])),
                   dict(sources=[dict(file=[], page=1, region='图', text='尺寸')]),
                   dict(sources=[dict(file='synthetic.pdf', page=[], region='图', text='尺寸')])]
        for patch in patches:
            with self.subTest(patch=patch):
                bad = item(id='Q2', instance_key='C2', evidence=proof())
                bad.update(patch)
                result = prepare(data(item(evidence=proof()), bad, evidence_policy=2))
                self.assertEqual([r['id'] for r in result['rows']], ['Q1'])
                self.assertEqual([r['id'] for r in result['review']], ['Q2'])

    def test_unconsumed_invalid_display_dimension_is_blank(self):
        result = prepare(data3(row3(depth_mm='600?')))
        self.assertFalse(result['review'])
        self.assertIsNone(result['rows'][0]['depth_mm'])
        self.assertEqual(result['rows'][0]['unverified_fields']['depth_mm'], '600?')

    def test_non_text_provenance_cannot_bypass_measurement_gates(self):
        for patch in ({'user_confirmation': True}, {'repeat_count': 2, 'repeat_evidence': True},
                      {'sources': [dict(file='synthetic.pdf', page=1, region={'a': 1}, text='2400')]},
                      {'evidence': proof(basis='user_confirmed'), 'user_confirmation': {'ok': True}}):
            bad = item(id='Q2', instance_key='C2', evidence=proof())
            bad.update(patch)
            result = prepare(data(item(evidence=proof()), bad, evidence_policy=2))
            self.assertEqual([r['id'] for r in result['review']], ['Q2'])

    def test_orphan_page_does_not_complete_product_coverage(self):
        project = data3(row3(unit_price=10, price_unit='平米', price_note='用户：10元'))
        project['documents'][0]['page_count'] = 2
        project['pages'].append(dict(file='synthetic.pdf', page=2, page_type='product_drawing', views=[]))
        result = prepare(project)
        self.assertTrue(result['page_coverage_complete'])
        self.assertFalse(result['product_coverage_complete'])
        self.assertFalse(result['coverage_complete'])
        self.assertIsNone(result['complete_total'])

    def test_view_disposition_links_exist_and_cover_item(self):
        for views in ([dict(view_id='A', disposition='measured', item_ids=['MISSING'], reason='立面')],
                      [dict(view_id='A', disposition='unresolved', item_ids=[], reason='无法配对')]):
            project = data3(row3())
            project['pages'][0]['views'] = views
            self.assertFalse(prepare(project)['product_coverage_complete'])

    def test_accessory_disposition_must_cover_all_types_and_children(self):
        row = row3()
        for checks in (None, dict(checked_types=['drawer'], present={}, unresolved={}, note='检查'),
                       dict(checked_types=ACCESSORIES, present={}, unresolved={'light_strip': '仅符号候选'}, note='待复核'),
                       dict(checked_types=ACCESSORIES, present={'drawer': ['missing']}, unresolved={}, note='检查')):
            row['accessory_checks'] = checks
            self.assertFalse(prepare(data3(row))['accessory_coverage_complete'])

    def test_legacy_calculations_remain_usable_without_final_total(self):
        for policy in (1, 2):
            row = item(evidence=proof(), unit_price=10, price_unit='平米', price_note='用户：10元')
            result = prepare(data(row, evidence_policy=policy))
            self.assertEqual(result['priced_subtotal'], 62.4)
            self.assertIsNone(result['complete_total'])


class FieldConfirmationTests(unittest.TestCase):
    def confirm(self, row, field='height_mm', value=2500):
        row['user_confirmation'] = '用户补充取量口径，逐字段记录'
        row['evidence']['basis'] = 'user_confirmed'
        row['evidence']['field_confirmations'] = [dict(field=field, value=value,
            reason='采用用户现场复尺值', user_confirmation=f'用户：{field}按{value}')]
        return row

    def test_blanket_confirmation_cannot_replace_all_evidence(self):
        row = row3(user_confirmation='用户：高度按2500', height_mm=2500)
        row['evidence'].update(basis='user_confirmed', dimension_readings=[], chain_check=[])
        result = prepare(data3(row))
        self.assertEqual(len(result['review']), 1)
        self.assertIn('field_confirmations', result['review'][0]['reason'])

    def test_height_override_preserves_original_reading_and_source_conflict(self):
        row = row3()
        row['height_mm'] = 2500
        self.confirm(row)
        row['evidence']['source_checks'] = [dict(field='height_mm', status='conflict', reason='文字层尺寸2600，与当前2500不符', observed_mm=2600)]
        result = prepare(data3(row))
        self.assertFalse(result['review'])
        calculated = result['rows'][0]
        self.assertEqual(calculated['quantity'], 6)
        self.assertEqual(calculated['height_mm'], 2500)
        self.assertEqual(row['evidence']['dimension_readings'][1]['value_mm'], 2600)
        self.assertTrue(calculated['evidence_warnings'])
        self.assertTrue(all(w['resolution'] == 'user_confirmed' for w in calculated['evidence_warnings']))
        self.assertIn('2600', str([w['original_values'] for w in calculated['evidence_warnings']]))

    def test_height_confirmation_does_not_clear_width_ambiguity(self):
        row = self.confirm(row3(height_mm=2500))
        row['evidence']['unresolved_issues'] = [dict(field='length_mm', reason='总宽仍待确认')]
        result = prepare(data3(row))
        self.assertEqual(len(result['review']), 1)
        self.assertIn('length_mm', result['review'][0]['reason'])

    def test_unconfirmed_width_still_needs_reading_and_chain(self):
        for missing in ('dimension_readings', 'chain_check'):
            row = self.confirm(row3(height_mm=2500))
            row['evidence'][missing] = []
            result = prepare(data3(row))
            self.assertEqual(len(result['review']), 1)
            self.assertIn('length_mm', result['review'][0]['reason'])

    def test_scoped_height_can_replace_missing_height_evidence_only(self):
        row = self.confirm(row3(height_mm=2500))
        row['evidence']['dimension_readings'] = row['evidence']['dimension_readings'][:1]
        row['evidence']['chain_check'] = row['evidence']['chain_check'][:1]
        result = prepare(data3(row))
        self.assertFalse(result['review'])
        self.assertEqual(result['rows'][0]['quantity'], 6)

    def test_confirmation_value_and_shape_must_match_current_field(self):
        for patch in ({'value': 2499}, {'value': True}, {'field': 'all'}, {'reason': ''}, {'user_confirmation': True}):
            row = self.confirm(row3(height_mm=2500))
            row['evidence']['field_confirmations'][0].update(patch)
            self.assertEqual(len(prepare(data3(row))['review']), 1)
        row['evidence']['field_confirmations'] = [None]
        self.assertEqual(len(prepare(data3(row))['review']), 1)

    def test_count_confirmation_cannot_be_inferred_from_other_field(self):
        row = child()
        row['evidence'] = evidence3(row, [], basis='user_confirmed')
        self.confirm(row, 'height_mm', row['height_mm'])
        result = prepare(data3(row3(), row))
        self.assertEqual([r['id'] for r in result['review']], ['A1'])
        self.confirm(row, 'count', 3)
        result = prepare(data3(row3(), row))
        self.assertFalse(result['review'])
        self.assertEqual(result['rows'][1]['quantity'], 3)

    def test_count_override_preserves_disagreeing_marks_as_resolved_warning(self):
        row = child(count=4)
        row['evidence'] = evidence3(row, [], basis='user_confirmed', boundary='not_applicable',
            count_marks=[dict(quantity=3, label='抽屉×3', region='下部', source_index=0)])
        self.confirm(row, 'count', 4)
        result = prepare(data3(row3(), row))
        self.assertFalse(result['review'])
        self.assertEqual(result['rows'][1]['quantity'], 4)
        self.assertEqual(result['rows'][1]['evidence_warnings'][0]['resolution'], 'user_confirmed')

    def test_boundary_requires_its_own_confirmation(self):
        row = self.confirm(row3(height_mm=2500))
        row['evidence']['boundary'] = 'uncertain'
        self.assertEqual(len(prepare(data3(row))['review']), 1)
        row['evidence']['boundary'] = 'clear'
        row['evidence']['unresolved_issues'] = [dict(field='boundary', reason='原图封板边界不清')]
        row['evidence']['field_confirmations'].append(dict(field='boundary', value='clear', reason='用户确认包含封板', user_confirmation='用户：封板计入'))
        self.assertFalse(prepare(data3(row))['review'])

    def test_legacy_user_confirmation_compatibility(self):
        row = item(user_confirmation='用户确认整柜尺寸', evidence=proof(basis='user_confirmed'))
        self.assertFalse(prepare(data(row, evidence_policy=2))['review'])


if __name__ == '__main__':
    unittest.main()
