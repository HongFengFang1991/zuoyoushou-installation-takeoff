"""Inclusive draft quantities never silently become verified observations."""
import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET
import xlsxwriter
import export_quote as eq
from prepare_quote import prepare
from test_prepare_quote import data, item
from test_workbook_v13 import edit_cell


class InclusiveQuoteTests(unittest.TestCase):
    def test_summary_sources_show_dimensions_and_quantities_with_links(self):
        r=self.result(item(needs_review=True,review_reason='待核尺寸'))
        path,_=self.export(r); cells=eq.read_cells(path)
        note=cells['报价汇总']['G7']
        self.assertIn('[Q1] 主卧 / 衣柜',note['value'])
        self.assertIn('长2400×高2600×深/宽600mm',note['value'])
        self.assertIn('6.240㎡【待复核·暂计】',note['value'])
        self.assertIn("'报价明细'!T7",note['formula'])
        self.assertIn('H7',cells['报价明细']['T7']['formula'])
        self.assertIn('｜第1页',note['value'])
        self.assertIn('SUBSTITUTE(R7',cells['报价明细']['T7']['formula'])

    def test_summary_sources_include_unique_pages_and_file_names(self):
        row=self.result(item())['quote_rows'][0]
        row['sources']=[dict(file='A.pdf',page=3),dict(file='A.pdf',page=1),
                        dict(file='A.pdf',page=3),dict(file='B.pdf',page=2)]
        note,formula=eq.source_note(row,7,True)
        self.assertTrue(note.endswith('A.pdf：第1、3页；B.pdf：第2页'))
        self.assertIn('SUBSTITUTE(R7,CHAR(10)',formula)

    def test_location_reference_does_not_change_drawer_price_group(self):
        from quotation_view import pricing_scope
        self.assertEqual(pricing_scope(dict(product_type='drawer',name='裤抽上方带锁抽面候选')), '默认')
        self.assertEqual(pricing_scope(dict(product_type='drawer',name='裤抽*2')), '裤抽')
        self.assertEqual(pricing_scope(dict(product_type='drawer',name='裤抽*2',price_scope='用户自定')), '用户自定')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = dict(project_id=str(uuid.uuid4()), source_fingerprint='b'*64, revision=1)

    def tearDown(self): self.temp.cleanup()

    def result(self, *items):
        result = prepare(data(*items))
        result.update(self.identity)
        return result

    def export(self, result, name='quote'):
        path = self.root / (name+'.xlsx')
        meta = eq.export_workbook(result, path)
        path.with_suffix('.manifest.json').write_text(json.dumps(meta, ensure_ascii=False))
        return path, meta

    def test_review_dimensions_and_drawers_are_quoted_once(self):
        r=self.result(item(needs_review=True,review_reason='总标与分段冲突'),
            item(id='A1',instance_key='A1',product_type='drawer',parent_id='Q1',name='抽屉候选',
                 count=3,needs_review=True,review_reason='仅图形候选'))
        self.assertEqual(len(r['rows']),0)
        self.assertEqual([v['quantity'] for v in r['quote_rows']],[6.24,3])
        path,meta=self.export(r);cells=eq.read_cells(path)
        self.assertEqual(set(cells),{'报价明细','报价汇总'})
        self.assertEqual(cells['报价明细']['H7']['value'],6.24)
        self.assertEqual(cells['报价明细']['K7']['value'],'待复核·已暂计')
        self.assertIn('总标与分段冲突',cells['报价明细']['L7']['value'])
        self.assertIn('主卧 / 衣柜',cells['报价汇总']['G7']['value'])
        self.assertEqual(cells['报价汇总']['E7']['value'],6.24)
        self.assertEqual(cells['报价明细']['J7']['value'],'')
        self.assertIsNone(r['complete_total'])

    def test_missing_dimensions_keeps_row_and_never_becomes_zero(self):
        r=self.result(item(length_mm=None,needs_review=True,review_reason='缺长度',
                           unit_price=35,price_unit='平米',price_note='用户报价'))
        path,_=self.export(r);cells=eq.read_cells(path)
        self.assertEqual(cells['报价明细']['H7']['value'],'')
        self.assertEqual(cells['报价明细']['J7']['value'],'')
        self.assertEqual(cells['报价汇总']['E7']['value'],'')
        self.assertEqual(cells['报价汇总']['F7']['value'],'')
        self.assertEqual(cells['报价明细']['K7']['value'],'待复核·未计量')

    def test_duplicate_entities_are_visible_but_not_double_charged(self):
        r=self.result(item(),item(id='Q2'))
        self.assertEqual(len(r['quote_rows']),2)
        self.assertTrue(all(v['quantity'] is None for v in r['quote_rows']))

    def test_pending_price_and_note_survive_repeated_exports(self):
        r=self.result(item(needs_review=True,review_reason='待核尺寸'))
        path,_=self.export(r)
        oldnote=eq.read_cells(path)['报价明细']['L7']['value']
        edit_cell(path,2,'D7',35);edit_cell(path,1,'L7',oldnote+'\n客户备注')
        info=eq.reconcile_previous(path,r)
        self.assertEqual(info['item_patches']['Q1']['remark'],'客户备注')
        self.assertEqual(info['item_patches']['Q1']['unit_price'],35)
        new=self.root/'new.xlsx';meta=eq.export_workbook(r,new,info)
        cells=eq.read_cells(new)
        self.assertEqual(cells['报价明细']['J7']['value'],218.4)
        self.assertEqual(cells['报价汇总']['F7']['value'],218.4)
        self.assertEqual(cells['报价明细']['L7']['value'].count('待复核：'),1)
        self.assertIn('暂计',cells['报价明细']['K7']['value'])

    def test_legacy_four_sheet_workbook_price_is_imported(self):
        r=self.result(item())
        path,meta=self.export(r)
        # Construct the old layout from a synthetic baseline, with old G group ID.
        cells=meta['baseline_cells'];cells['待复核']={};cells['资料清单']={}
        for addr in list(cells['报价明细']):
            if addr.startswith('S'):del cells['报价明细'][addr]
        cells['报价汇总']['G7']=cells['报价汇总'].pop('H7')
        with xlsxwriter.Workbook(str(path)) as wb:
            for key in ('project_id','source_fingerprint','revision','workbook_id'):
                wb.set_custom_property('takeoff_'+key,str(meta[key]))
            for name in ('报价明细','报价汇总','待复核','资料清单'):
                sheet=wb.add_worksheet(name)
                for address,cell in cells[name].items():
                    if cell.get('formula'):sheet.write_formula(address,'='+cell['formula'],None,cell['value'])
                    else:sheet.write(address,cell['value'])
        meta.update(version='1.3.0',baseline_cells=eq.read_cells(path),review={})
        path.with_suffix('.manifest.json').write_text(json.dumps(meta))
        edit_cell(path,2,'D7',40)
        info=eq.reconcile_previous(path,r)
        self.assertEqual(info['item_patches']['Q1']['unit_price'],40)

    def test_row_rounded_amounts_reconcile_with_summary(self):
        r=self.result(item(length_mm=2449,height_mm=2500,needs_review=True,
            review_reason='待核',unit_price=35,price_unit='平米',price_note='用户报价'))
        self.assertEqual(r['quote_rows'][0]['quantity'],6.123)
        self.assertEqual(r['quote_rows'][0]['amount'],214.31)
        self.assertEqual(r['quote_summary'][0]['amount'],214.31)

    def test_page_coverage_uses_measuring_sources_and_status_colors(self):
        raw=data(item(),item(id='Q2',instance_key='Q2',needs_review=True,review_reason='待核'),
                 documents=[dict(file='synthetic.pdf',page_count=3)])
        raw['pages'] += [dict(file='synthetic.pdf',page=2,page_type='spatial_plan'),
                         dict(file='synthetic.pdf',page=3,page_type='product_drawing')]
        raw['items'][0]['sources'] += [dict(file='synthetic.pdf',page=2,role='context',region='平面',text='仅参考')]
        raw['items'][1]['sources'] += [dict(file='synthetic.pdf',page=3,role='measurement',region='立面',text='2400x2600')]
        r=prepare(raw);r.update(self.identity)
        path,_=self.export(r);cells=eq.read_cells(path)
        self.assertIn('PDF总页数：3页；参与计算：2页',cells['报价明细']['A3']['value'])
        self.assertIn('页码：1、3',cells['报价明细']['A3']['value'])
        with zipfile.ZipFile(path) as z:
            style=ET.fromstring(z.read('xl/styles.xml'))
            fills=style.find('s:fills',eq.NS);xfs=style.find('s:cellXfs',eq.NS)
            grid=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            colors={}
            for cell in grid.findall('.//s:c',eq.NS):
                if cell.get('r') in ['A7','A8']:
                    fill=fills[int(xfs[int(cell.get('s'))].get('fillId'))]
                    colors[cell.get('r')]=fill.find('.//s:fgColor',eq.NS).get('rgb')
            self.assertEqual(colors,{'A7':'FFE2F0D9','A8':'FFFCE4D6'})


if __name__=='__main__': unittest.main()
