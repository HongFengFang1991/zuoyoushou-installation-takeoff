"""Portable workflow regressions. Run with unittest discovery in scripts/."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from test_prepare_quote import data, item
from test_measurement_v13 import row3, evidence3
import prepare_quote as q


def proof(**kw):
    p = dict(basis="explicit_dimensions", boundary="clear", note="尺寸线覆盖本柜外轮廓",
             dimension_checks=[], adjustments=[], ambiguities=[])
    p.update(kw)
    return p


class EvidenceTests(unittest.TestCase):
    def test_new_policy_requires_evidence(self):
        r = q.prepare(data(item(), evidence_policy=2))
        self.assertEqual(len(r['review']), 1)

    def test_conflicting_chain_not_bypassed_by_confidence(self):
        p = proof(dimension_checks=[dict(field="length_mm", overall_mm=2400, segments_mm=[1200, 1198])])
        r = q.prepare(data(item(evidence=p, confidence=1), evidence_policy=2))
        self.assertEqual(len(r['review']), 1)

    def test_resolved_chain_must_match_measurement(self):
        p=proof(dimension_checks=[dict(field='length_mm',overall_mm=2400,segments_mm=[1200,1200])])
        r=q.prepare(data(item(length_mm=2500,evidence=p),evidence_policy=2))
        self.assertEqual(len(r['review']),1)

    def test_candidate_count_not_billed(self):
        a = item(evidence=proof())
        b = item(id="A1", instance_key="A1", parent_id="Q1", product_type="drawer", count=3,
                 evidence=proof(basis="visual_candidate", boundary="not_applicable"))
        r = q.prepare(data(a, b, evidence_policy=2))
        self.assertEqual([x['id'] for x in r['review']], ['A1'])

    def test_explicit_marks_and_corner_adjustment(self):
        a = item(length_mm=1738, evidence=proof(adjustments=[dict(field="length_mm", original_mm=2398,
                 deduction_mm=660, evidence="相邻柜侧投影尺寸660，跨页结构与外轮廓已核对")]))
        b = item(id="A1", instance_key="A1", parent_id="Q1", product_type="drawer", count=3,
                 evidence=proof(basis="explicit_count", boundary="not_applicable",
                                count_marks=[dict(label="抽屉×3", quantity=3, region="下部")]))
        r = q.prepare(data(a, b, evidence_policy=2))
        self.assertFalse(r['review'])
        b['count'] = 2
        self.assertEqual(len(q.prepare(data(a, b, evidence_policy=2))['review']), 1)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        import takeoff_runtime as rt
        self.rt = rt
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        from PIL import Image
        self.src = self.root / '图纸.png'
        Image.new('RGB', (800, 500), 'white').save(self.src)
        self.project = self.root / '项目'

    def tearDown(self):
        self.tmp.cleanup()

    def start(self):
        return self.rt.initialize(self.project, [self.src], '合成工程')

    def batch(self, state):
        name = state['documents'][0]['file']
        row = row3(sources=[dict(file=name, page=1, region="本柜", text="2400×2600")])
        return dict(batch_id='b1', pages=[dict(file=name, page=1, page_type='product_drawing', reason='已看原图', views=[dict(view_id='front',disposition='measured',item_ids=['Q1'],reason='正立面')])],
                    items=[row])

    def test_initialize_preflight_and_cache(self):
        s = self.start()
        self.assertTrue((self.project / 'preflight.json').exists())
        pages = self.rt.render_pages(self.project)
        image = self.project / pages[0]['image']
        stamp = image.stat().st_mtime_ns
        self.rt.render_pages(self.project)
        self.assertEqual(image.stat().st_mtime_ns, stamp)
        self.assertEqual(self.rt.status(self.project)['remaining_pages'], 1)

    def test_checkpoint_idempotent_and_changed_source_rejected(self):
        s = self.start()
        self.rt.render_pages(self.project)
        b = self.batch(s)
        self.rt.checkpoint(self.project, b)
        self.rt.checkpoint(self.project, b)
        self.assertEqual(self.rt.status(self.project)['remaining_pages'], 0)
        b2 = copy.deepcopy(b); b2['items'][0]['length_mm'] = 2000
        with self.assertRaises(ValueError):
            self.rt.checkpoint(self.project, b2)
        (self.project / s['documents'][0]['stored_path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.rt.render_pages(self.project)

    def test_revision_changes_only_quantity_without_rendering(self):
        s = self.start(); self.rt.render_pages(self.project)
        b = self.batch(s); b['items'][0].update(length_mm=800, height_mm=900); b['items'][0]['evidence']=evidence3(b['items'][0])
        self.rt.checkpoint(self.project, b)
        before = q.prepare(self.rt.load_takeoff(self.project))
        patch = dict(batch_id='r2', reason='本项目矮柜按实际米数', settings=dict(rule_overrides=[dict(
            id='actual', layer='item', selector={'id':'Q1'}, method='length', unit='米', minimum=0,
            source_text='本项目矮柜按实际米数')]))
        self.rt.checkpoint(self.project, patch)
        after = q.prepare(self.rt.load_takeoff(self.project))
        self.assertEqual((before['rows'][0]['quantity'], after['rows'][0]['quantity']), (1, .8))
        self.assertTrue((self.project / 'changes' / 'r2.json').exists())

    def test_export_embedded_thumbnails_prices_and_preservation(self):
        s = self.start(); self.rt.render_pages(self.project)
        b = self.batch(s); self.rt.checkpoint(self.project, b)
        self.rt.crop(self.project,'Q1','D001',1,[.05,.05,.95,.95])
        path = self.rt.export_project(self.project)
        from export_quote import read_cells
        cells = read_cells(path)
        self.assertEqual(cells['报价明细']['H7']['value'], 6.24)
        self.assertEqual(cells['报价明细']['J7']['value'], '')
        self.assertIn('SUM', cells['报价汇总']['E7']['formula'])
        self.assertEqual(cells['报价明细']['A8']['value'], '合计')
        with zipfile.ZipFile(path) as z:
            self.assertGreaterEqual(len([n for n in z.namelist() if n.startswith('xl/media/')]), 2)
        # Same physical file can be exported again without re-reading drawings.
        self.assertTrue(self.rt.export_project(self.project, previous=path).exists())

    def edit_cell(self, path, sheet, address, value):
        from export_quote import NS
        with zipfile.ZipFile(path) as z:
            entries = {n:z.read(n) for n in z.namelist()}
        name = f'xl/worksheets/sheet{sheet}.xml'
        xml = ET.fromstring(entries[name])
        node = next(n for n in xml.findall('.//s:c',NS) if n.attrib['r']==address)
        for c in list(node): node.remove(c)
        if isinstance(value,str):
            node.set('t','inlineStr'); el=ET.SubElement(node,'{'+NS['s']+'}is')
            ET.SubElement(el,'{'+NS['s']+'}t').text=value
        else:
            node.attrib.pop('t',None); ET.SubElement(node,'{'+NS['s']+'}v').text=str(value)
        entries[name]=ET.tostring(xml,encoding='utf-8',xml_declaration=True)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for n,v in entries.items(): z.writestr(n,v)

    def test_manual_group_price_zero_override_and_note_preserved(self):
        from export_quote import read_cells
        s=self.start(); self.rt.render_pages(self.project); b=self.batch(s)
        second=copy.deepcopy(b['items'][0]); second.update(id='Q2',instance_key='Q2')
        b['items'].append(second); self.rt.checkpoint(self.project,b)
        path=self.rt.export_project(self.project)
        self.edit_cell(path,2,'D7',35)
        self.edit_cell(path,1,'I7',0)
        self.edit_cell(path,1,'L7','用户免收第一项')
        new=self.rt.export_project(self.project,previous=path); cells=read_cells(new)
        self.assertEqual(cells['报价明细']['J7']['value'],0)
        self.assertEqual(cells['报价明细']['J8']['value'],218.4)
        self.assertEqual(cells['报价汇总']['F7']['value'],218.4)
        self.assertEqual(cells['报价明细']['L7']['value'],'用户免收第一项')
        calc=self.rt.read_json(new.parent/'calculation/calculated.json')
        self.assertEqual(calc['priced_subtotal'],218.4)
        again=self.rt.export_project(self.project,previous=new)
        self.assertEqual(read_cells(again)['报价汇总']['F7']['value'],218.4)
        self.edit_cell(new,1,'H7',999)
        with self.assertRaisesRegex(ValueError,'数量'):
            self.rt.export_project(self.project,previous=new)

    def test_new_prices_group_isolation_and_large_exports(self):
        from export_quote import read_cells
        s=self.start(); self.rt.render_pages(self.project); b=self.batch(s)
        a=b['items'][0]; a.update(unit_price=35,price_unit='平米',price_note='每平米35元')
        for i in range(1,65):
            r=copy.deepcopy(a); r.update(id=f'Q{i+1}',instance_key=f'C{i+1}')
            if i%2: r.update(product_type='wall_panel',unit_price=0,price_note='墙板免费')
            b['items'].append(r)
        self.rt.checkpoint(self.project,b)
        path=self.rt.export_project(self.project); c=read_cells(path)
        self.assertEqual(c['报价明细']['A72']['value'],'合计')
        self.assertEqual(c['报价汇总']['F7']['value'],7207.2)
        meta=self.rt.read_json(path.with_suffix('.manifest.json'))
        free_group=next(v for v in meta['groups'].values() if v['price']==0)
        self.assertEqual(c['报价汇总'][f"F{free_group['row_number']}"]['value'],0)
        notes='\n'.join(v['value'] for key,v in c['报价汇总'].items() if key.startswith('G') and isinstance(v['value'],str))
        for i in range(1,66): self.assertEqual(notes.count(f'[Q{i}]'),1)
        self.assertEqual(c['报价汇总']['E7']['value'],205.92)

    def test_failed_checkpoint_preserves_state_and_export_failure_resumes(self):
        from unittest.mock import patch
        s=self.start(); self.rt.render_pages(self.project)
        before=(self.project/'state.json').read_bytes()
        b=self.batch(s); b['pages'][0]['page']=99
        with self.assertRaises(ValueError): self.rt.checkpoint(self.project,b)
        self.assertEqual(before,(self.project/'state.json').read_bytes())
        self.rt.checkpoint(self.project,self.batch(s))
        with patch('export_quote.export_workbook',side_effect=PermissionError('simulated write denied')):
            with self.assertRaises(PermissionError): self.rt.export_project(self.project)
        self.assertTrue(self.rt.export_project(self.project).exists())

    def test_cache_damage_and_duplicate_ids(self):
        s=self.start(); images=self.rt.render_pages(self.project)
        p=self.project/images[0]['image']; p.write_bytes(b'bad')
        self.rt.render_pages(self.project)
        from PIL import Image
        with Image.open(p) as im: self.assertEqual(im.size,(800,500))
        b=self.batch(s); b['items'].append(copy.deepcopy(b['items'][0]))
        with self.assertRaisesRegex(ValueError,'重复'): self.rt.checkpoint(self.project,b)

    def test_crop_is_cached_and_bound_to_source(self):
        s=self.start(); self.rt.render_pages(self.project); self.rt.checkpoint(self.project,self.batch(s))
        result=self.rt.crop(self.project,'Q1','D001',1,[0,0,.5,.5])
        p=self.project/result['image']; stamp=p.stat().st_mtime_ns
        self.rt.crop(self.project,'Q1','D001',1,[0,0,.5,.5])
        self.assertEqual(stamp,p.stat().st_mtime_ns)
        dest=self.rt.export_project(self.project)
        manifest=self.rt.read_json(dest.with_suffix('.manifest.json'))
        self.assertEqual(Path(manifest['thumbnails'][0]['image']).resolve(),p.resolve())

    def test_manual_unregistered_item_and_amount_override_rejected(self):
        s=self.start(); self.rt.render_pages(self.project); self.rt.checkpoint(self.project,self.batch(s))
        dest=self.rt.export_project(self.project)
        self.edit_cell(dest,1,'O7','QEXTRA')
        with self.assertRaisesRegex(ValueError,'未登记|ID'):
            self.rt.export_project(self.project,previous=dest)
        fresh=self.rt.export_project(self.project)
        self.edit_cell(fresh,1,'J7',200)
        with self.assertRaisesRegex(ValueError,'金额'):
            self.rt.export_project(self.project,previous=fresh)


if __name__ == '__main__':
    unittest.main()
