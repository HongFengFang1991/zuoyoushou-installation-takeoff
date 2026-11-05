"""v1.3 workbook identity and three-way edit import regressions (synthetic only)."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
import zipfile

import export_quote as eq
import prepare_quote as q
from test_prepare_quote import data, item


def edit_cell(path, sheet, address, value, formula=None):
    with zipfile.ZipFile(path) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    member = f'xl/worksheets/sheet{sheet}.xml'
    tree = ET.fromstring(entries[member])
    sheet_data = tree.find('s:sheetData', eq.NS)
    number = ''.join(c for c in address if c.isdigit())
    row = next((n for n in sheet_data if n.get('r') == number), None)
    if row is None:
        row = ET.SubElement(sheet_data, '{'+eq.NS['s']+'}row', r=number)
    cell = next((n for n in row if n.get('r') == address), None)
    if cell is None:
        cell = ET.SubElement(row, '{'+eq.NS['s']+'}c', r=address)
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop('t', None)
    if formula is not None:
        ET.SubElement(cell, '{'+eq.NS['s']+'}f').text = formula
        ET.SubElement(cell, '{'+eq.NS['s']+'}v').text = str(value)
    elif isinstance(value, str):
        cell.set('t', 'inlineStr')
        inline = ET.SubElement(cell, '{'+eq.NS['s']+'}is')
        ET.SubElement(inline, '{'+eq.NS['s']+'}t').text = value
    else:
        ET.SubElement(cell, '{'+eq.NS['s']+'}v').text = str(value)
    entries[member] = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in entries.items():
            z.writestr(name, content)


class WorkbookV13Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = data(item(unit_price=35, price_unit='平米', price_note='合成报价'))
        self.identity = dict(project_id=str(uuid.uuid4()), source_fingerprint='a'*64, revision=1)

    def tearDown(self):
        self.tmp.cleanup()

    def result(self, raw=None, **kw):
        value = q.prepare(raw or self.raw)
        value.update(self.identity, all_items=copy.deepcopy((raw or self.raw)['items']))
        value.update(kw)
        return value

    def export(self, result=None, name='quote', manual=None):
        path = self.root / (name+'.xlsx')
        meta = eq.export_workbook(result or self.result(), path, manual)
        path.with_suffix('.manifest.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        return path, meta

    def test_untouched_old_price_does_not_conflict_with_new_price(self):
        path, _ = self.export()
        edit_cell(path, 1, 'L7', '客户手改备注')
        current = copy.deepcopy(self.raw)
        current['items'][0].update(unit_price=40)
        merged = eq.reconcile_previous(path, self.result(current, revision=2))
        self.assertEqual(merged['effective_prices']['Q1'], 40)
        self.assertNotIn('unit_price', merged['item_patches']['Q1'])
        self.assertEqual(merged['item_patches']['Q1']['remark'], '客户手改备注')

    def test_drawing_pages_are_explicit_and_read_only(self):
        path, meta = self.export()
        cells = eq.read_cells(path)['报价明细']
        self.assertEqual(cells['R6']['value'], '图纸页码')
        expected = eq.drawing_pages(self.raw['items'][0])
        self.assertEqual(cells['R7']['value'], expected)
        self.assertEqual(meta['baseline_cells']['报价明细']['R7']['value'], expected)
        edit_cell(path, 1, 'R7', '第999页')
        with self.assertRaises(ValueError):
            eq.reconcile_previous(path, self.result())

    def test_drawing_pages_group_duplicates_and_do_not_guess(self):
        row = {'sources':[{'file':'A.pdf','page':3},{'file':'A.pdf','page':1},
                          {'file':'A.pdf','page':3}]}
        self.assertEqual(eq.drawing_pages(row), '第1、3页')
        row['sources'].append({'file':'B.pdf','page':3})
        self.assertEqual(eq.drawing_pages(row), 'A.pdf：第1、3页\nB.pdf：第3页')
        self.assertEqual(eq.drawing_pages({'sources':[{'file':'A.pdf','page':2}]}, True), 'A.pdf：第2页')
        self.assertEqual(eq.drawing_pages({'id':'Q022'}), '待补页码')

    def test_untouched_note_keeps_new_state_note(self):
        self.raw['items'][0]['remark'] = '旧备注'
        path, _ = self.export()
        current = copy.deepcopy(self.raw)
        current['items'][0]['remark'] = '需提前预约电梯'
        merged = eq.reconcile_previous(path, self.result(current, revision=2))
        out, _ = self.export(self.result(current, revision=2), 'new', merged)
        self.assertEqual(eq.read_cells(out)['报价明细']['L7']['value'], '需提前预约电梯')
        self.assertFalse(merged['notes'])

    def test_same_old_numeric_price_is_not_a_new_price_edit(self):
        path, _ = self.export()
        edit_cell(path, 1, 'I7', 35)
        current = copy.deepcopy(self.raw)
        current['items'][0]['unit_price'] = 40
        merged = eq.reconcile_previous(path, self.result(current, revision=2))
        self.assertEqual(merged['effective_prices']['Q1'], 40)
        self.assertFalse(merged['item_patches'])

    def test_concurrent_price_or_note_edits_conflict(self):
        for col, field, workbook, current in [('I','unit_price',45,40), ('L','remark','工作簿备注','台账备注')]:
            with self.subTest(col=col):
                path, _ = self.export(name=col)
                edit_cell(path, 1, col+'7', workbook)
                new = copy.deepcopy(self.raw)
                new['items'][0][field] = current
                with self.assertRaisesRegex(ValueError, '冲突'):
                    eq.reconcile_previous(path, self.result(new, revision=2))

    def test_wrong_project_same_title_and_rows_rejected(self):
        path, _ = self.export()
        with self.assertRaisesRegex(ValueError, '项目|UUID'):
            eq.reconcile_previous(path, self.result(project_id=str(uuid.uuid4())))

    def test_workbook_manifest_pairing_and_legacy_are_rejected(self):
        path, meta = self.export()
        other, othermeta = self.export(name='other')
        path.with_suffix('.manifest.json').write_text(json.dumps(othermeta), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '配对|标识'):
            eq.reconcile_previous(path, self.result())
        meta.pop('project_id', None)
        meta['version'] = '1.2.0'
        path.with_suffix('.manifest.json').write_text(json.dumps(meta), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '旧版|逐行|手工'):
            eq.reconcile_previous(path, self.result())

    def test_all_unsupported_visible_edits_are_reported(self):
        cases = [(2,'E7',999),(2,'F7',9999),(2,'F8',9999),(2,'A7','额外服务'),
                 (1,'J8',9999),(1,'H7',999),(1,'N7','来源已改'),(1,'R7','伪称页码'),
                 (2,'H20','汇总之外新增收费'),(1,'A1','新标签')]
        for index, (sheet, cell, value) in enumerate(cases):
            with self.subTest(cell=cell, sheet=sheet):
                path, _ = self.export(name=f'unsupported-{index}')
                edit_cell(path, sheet, cell, value)
                with self.assertRaisesRegex(ValueError, '修改|改写|变化|新增'):
                    eq.reconcile_previous(path, self.result())

    def test_group_price_and_free_override_are_real_patches(self):
        second = copy.deepcopy(self.raw['items'][0]); second.update(id='Q2', instance_key='Q2')
        self.raw['items'].append(second)
        path, _ = self.export()
        edit_cell(path, 2, 'D7', 40)
        edit_cell(path, 1, 'I7', 0)
        merged = eq.reconcile_previous(path, self.result())
        self.assertEqual(merged['effective_prices'], {'Q1':0,'Q2':40})
        self.assertEqual(merged['item_patches']['Q2']['unit_price'], 40)

    def test_pending_review_text_is_preserved_without_confirmation(self):
        self.raw['items'][0].update(needs_review=True, review_reason='尺寸需核对')
        path, _ = self.export()
        edit_cell(path, 1, 'S7', '用户确认：宽2400，高2600，按图计算')
        merged = eq.reconcile_previous(path, self.result())
        pending = merged['pending_review_inputs']
        self.assertEqual(pending[0]['row_id'], 'Q1')
        self.assertEqual(pending[0]['status'], 'pending')
        self.assertNotIn('user_confirmation', merged['item_patches'].get('Q1', {}))
        new, meta = self.export(self.result(pending_review_inputs=pending), 'review-again', merged)
        self.assertEqual(eq.read_cells(new)['报价明细']['S7']['value'], pending[0]['text'])
        self.assertTrue(meta['warnings'])

    def test_note_on_row_now_in_review_is_not_lost(self):
        path, _ = self.export()
        edit_cell(path, 1, 'L7', '确认后按特殊工艺报价')
        current = copy.deepcopy(self.raw)
        current['items'][0].update(needs_review=True, review_reason='尺寸需核对')
        merged = eq.reconcile_previous(path, self.result(current, revision=2))
        self.assertEqual(merged['item_patches']['Q1']['remark'], '确认后按特殊工艺报价')
        self.assertIn('Q1', merged['effective_prices'])
        again, _ = self.export(self.result(current, revision=2), 'moved-review', merged)
        self.assertIn('确认后按特殊工艺报价', eq.read_cells(again)['报价明细']['L7']['value'])

    def test_old_group_price_does_not_set_new_member_price(self):
        path, _ = self.export()
        edit_cell(path, 2, 'D7', 40)
        current = copy.deepcopy(self.raw)
        second = copy.deepcopy(current['items'][0])
        second.update(id='Q2', instance_key='Q2', unit_price=None, price_unit=None, price_note='')
        current['items'].append(second)
        new_result = self.result(current, revision=2)
        merged = eq.reconcile_previous(path, new_result)
        self.assertEqual(merged['effective_prices'], {'Q1':40, 'Q2':None})
        again, _ = self.export(new_result, 'new-member', merged)
        self.assertEqual(eq.read_cells(again)['报价明细']['I8']['value'], '')
        self.assertTrue(merged['warnings'])

    def test_pending_confirmation_can_be_cleared_without_confirming(self):
        self.raw['items'][0].update(needs_review=True, review_reason='尺寸需核对')
        pending = [dict(id='pending-1', row_id='Q1',text='旧人工输入',status='pending')]
        result = self.result(pending_review_inputs=pending)
        path, _ = self.export(result)
        edit_cell(path, 1, 'S7', '')
        merged = eq.reconcile_previous(path, result)
        self.assertEqual(merged['pending_review_inputs'][-1]['text'], '')
        self.assertFalse(merged['item_patches'])

    def test_formula_cache_recalculation_is_not_a_manual_edit(self):
        path, _ = self.export()
        formula = eq.read_cells(path)['报价汇总']['F7']['formula']
        edit_cell(path, 2, 'F7', 123, formula=formula)
        self.assertFalse(eq.reconcile_previous(path, self.result())['item_patches'])

    def test_summary_or_review_formula_is_never_evaluated(self):
        path, _ = self.export()
        edit_cell(path, 2, 'D7', 35, formula='1+34')
        with self.assertRaisesRegex(ValueError, '公式'):
            eq.reconcile_previous(path, self.result())
        self.raw['items'][0].update(needs_review=True, review_reason='尺寸需核对')
        path, _ = self.export(name='pending-formula')
        edit_cell(path, 1, 'S7', 35, formula='1+34')
        with self.assertRaisesRegex(ValueError, '公式'):
            eq.reconcile_previous(path, self.result())

    def test_changed_source_fingerprint_rejected(self):
        path, _ = self.export()
        with self.assertRaisesRegex(ValueError, '指纹'):
            eq.reconcile_previous(path, self.result(source_fingerprint='b'*64))

    def test_pending_review_concurrent_edits_conflict(self):
        self.raw['items'][0].update(needs_review=True, review_reason='尺寸需核对')
        path, _ = self.export()
        edit_cell(path, 1, 'S7', '表格确认宽2400')
        current = self.result(pending_review_inputs=[dict(row_id='Q1',text='台账确认宽2500',status='pending')])
        with self.assertRaisesRegex(ValueError, '冲突'):
            eq.reconcile_previous(path, current)

    def test_visible_ids_and_complete_manifest_baseline(self):
        path, meta = self.export()
        cells = eq.read_cells(path)
        self.assertIn('Q1', cells['报价明细']['N7']['value'])
        self.assertEqual(meta['version'], '1.4.1')
        self.assertEqual(meta['baseline_cells']['报价汇总']['F7']['formula'], cells['报价汇总']['F7']['formula'])
        with zipfile.ZipFile(path) as z:
            self.assertIn(self.identity['project_id'], z.read('docProps/custom.xml').decode())

    def test_coverage_and_unverified_dimensions_remain_visible(self):
        result = self.result(product_coverage_complete=False, accessory_coverage_complete=False,
                             coverage_issues=['Q1配件覆盖未确认'])
        result['rows'][0].update(depth_mm=None, evidence_warnings=[dict(field='depth_mm', reason='未核对原图')],
                                 unverified_fields={'depth_mm':600})
        path, meta = self.export(result)
        cells = eq.read_cells(path)
        self.assertIn('覆盖尚未核对完整', cells['报价明细']['A4']['value'])
        self.assertIn('depth_mm=600', cells['报价明细']['N7']['value'])
        self.assertIn('未用于本项取量', cells['报价明细']['N7']['value'])
        self.assertIn('Q1配件覆盖未确认', meta['warnings'])


if __name__ == '__main__':
    unittest.main()
