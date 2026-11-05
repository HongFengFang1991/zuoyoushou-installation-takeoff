"""Synthetic-only regressions for source indexes, review cards and entity benchmarks."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def synthetic_pdf(path, rotation=0, crop=(40, 30, 340, 230), text='2400 2600 600'):
    """Small actual PDF with native Type1 text; no extra test dependency."""
    stream = f'BT /F1 18 Tf 60 180 Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /CropBox [{" ".join(map(str,crop))}] /Rotate {rotation} /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'.encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    out=bytearray(b'%PDF-1.4\n'); offsets=[0]
    for i, obj in enumerate(objects,1):
        offsets.append(len(out)); out.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref=len(out); out.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: out.extend(f'{offset:010d} 00000 n \n'.encode())
    out.extend(f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(out)


class SourceToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def source(self):
        self.assertIsNotNone(importlib.util.find_spec('source_evidence'), 'source evidence helper is missing')
        import source_evidence
        return source_evidence

    def test_pdf_native_numbers_coords_match_render_rotation_crop(self):
        e=self.source()
        import pypdfium2 as pdfium
        for rotation in (0,90,180,270):
            path=self.root/f'rotation-{rotation}.pdf'; synthetic_pdf(path,rotation=rotation)
            idx=e.index_document_page(path,1)
            self.assertEqual(idx['rotation'],rotation)
            self.assertEqual(idx['coordinate_system'],'normalized_rendered_top_left')
            self.assertEqual([n['value'] for n in idx['numbers']],[2400,2600,600])
            self.assertTrue(idx['text_available']); self.assertEqual(len(idx['source_hash']),64)
            with pdfium.PdfDocument(str(path)) as pdf:
                page=pdf[0]; bitmap=page.render(scale=2); image=bitmap.to_pil().convert('L').copy()
                w,h=image.size
                for token in idx['numbers']:
                    x0,y0,x1,y1=token['box']
                    self.assertTrue(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)
                    crop=image.crop((max(0,int(x0*w)-1),max(0,int(y0*h)-1),min(w,int(x1*w)+2),min(h,int(y1*h)+2)))
                    self.assertLess(crop.getextrema()[0],100,'token box must actually contain rendered ink')
                bitmap.close(); page.close()
            self.assertEqual(e.query_index(idx,'2600',limit=1)['numbers'][0]['value'],2600)

    def test_native_mismatch_region_and_context_cannot_confirm(self):
        e=self.source(); path=self.root/'native.pdf'; synthetic_pdf(path)
        idx=e.index_document_page(path,1)
        row=dict(sources=[dict(file=path.name,page=1,role='measurement')],
                 evidence=dict(dimension_readings=[dict(field='length_mm',value_mm=2398,source_index=0,text='2398',region='顶部长尺寸')]))
        verdict=e.verify_readings(row,{(path.name,1):idx})
        self.assertEqual(verdict['checks'][0]['status'],'conflict'); self.assertTrue(verdict['issues'])
        row['evidence']['dimension_readings'][0]['value_mm']=2400
        row['evidence']['dimension_readings'][0]['text']='2400'
        verdict=e.verify_readings(row,{(path.name,1):idx})
        self.assertEqual(verdict['checks'][0]['status'],'matched'); self.assertTrue(verdict['warnings'])
        row['evidence']['dimension_readings'][0]['box']=[0,.8,1,1]
        self.assertEqual(e.verify_readings(row,{(path.name,1):idx})['checks'][0]['status'],'conflict')
        row['sources'][0]['role']='context'
        self.assertTrue(e.verify_readings(row,{(path.name,1):idx})['issues'])

    def test_scan_missing_index_and_malformed_readings_are_not_proof(self):
        e=self.source()
        from PIL import Image
        path=self.root/'scan.png'; Image.new('RGB',(100,60),'white').save(path)
        idx=e.index_document_page(path,1)
        self.assertFalse(idx['text_available']); self.assertEqual(idx['numbers'],[])
        row=dict(sources=[dict(file=path.name,page=1,role='measurement')],
                 evidence=dict(dimension_readings=[dict(field='length_mm',value_mm=2400,source_index=0,text='2400',region='上方')]))
        verdict=e.verify_readings(row,{(path.name,1):idx})
        self.assertEqual(verdict['checks'][0]['status'],'unavailable')
        self.assertTrue(verdict['warnings']); self.assertFalse(verdict['issues'])
        row['evidence']['dimension_readings'][0]['source_index']=99
        self.assertTrue(e.verify_readings(row,{})['issues'])

    def test_index_identity_and_malformed_source_are_local_conflicts(self):
        e=self.source(); path=self.root/'native.pdf'; synthetic_pdf(path)
        idx=e.index_document_page(path,1)
        row=dict(sources=[dict(file='different.pdf',page=1,role='measurement')],
                 evidence=dict(dimension_readings=[dict(field='length_mm',value_mm=2400,source_index=0,text='2400',region='上方')]))
        self.assertTrue(e.verify_readings(row,{0:idx})['issues'])
        row['sources']=[None]
        self.assertTrue(e.verify_readings(row,{0:idx})['issues'])
        row['evidence']=None
        self.assertTrue(e.verify_readings(row,{0:idx})['issues'])

    def test_units_and_reading_text_disagreement(self):
        e=self.source(); path=self.root/'units.pdf'; synthetic_pdf(path,text='2.4m 260cm 600mm')
        idx=e.index_document_page(path,1)
        row=dict(sources=[dict(file=path.name,page=1,role='measurement')],
                 evidence=dict(dimension_readings=[dict(field='length_mm',value_mm=2400,source_index=0,text='2.4m',region='上方')]))
        self.assertEqual(e.verify_readings(row,{(path.name,1):idx})['checks'][0]['status'],'matched')
        row['evidence']['dimension_readings'][0]['text']='2.6m'
        self.assertTrue(e.verify_readings(row,{(path.name,1):idx})['issues'])

    def test_review_html_embeds_and_escapes_and_binds_download(self):
        self.assertIsNotNone(importlib.util.find_spec('review_cards'),'review cards helper is missing')
        from review_cards import generate_review_cards
        from PIL import Image
        path=self.root/'thumb.png'; Image.new('RGB',(100,60),'white').save(path)
        result=dict(project='<工程>',project_id='project-1',revision=3,rows=[dict(id='Q1',name='<script>alert(1)</script>',
                    product_type='cabinet',quantity=6.24,unit='平米',thumbnail=str(path),length_mm=2400,
                    formula='2.4×2.6',evidence=dict(dimension_readings=[dict(field='length_mm',value_mm=2400,source_index=0,text='2400')]))],
                    review=[dict(id='Q2',name='待定柜',reason='缺尺寸')],excluded=[],pending_review_inputs=[dict(row_id='Q2',text='请核对')])
        before=copy.deepcopy(result); dest=generate_review_cards(result,self.root/'review.html'); html=Path(dest).read_text()
        self.assertEqual(result,before)
        self.assertIn('data:image/png;base64,',html); self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',html)
        self.assertNotIn('<script>alert(1)</script>',html); self.assertNotIn('https://',html)
        self.assertIn('"base_revision":3',html); self.assertIn('review_inputs',html)
        self.assertIn('row_id',html); self.assertIn('project-1',html); self.assertIn('Q1',html)
        self.assertIn('type="search"',html); self.assertIn('2400',html); self.assertIn('请核对',html)

    def test_benchmark_entity_errors_cannot_cancel_in_total(self):
        self.assertIsNotNone(importlib.util.find_spec('benchmark_takeoff'),'benchmark helper is missing')
        from benchmark_takeoff import compare_benchmark
        expected=dict(cases=[dict(id='Q1',product_type='cabinet',length_mm=2400,quantity=6.24,bucket='rows'),
                             dict(id='Q2',product_type='cabinet',length_mm=2600,quantity=6.76,bucket='rows'),
                             dict(id='Q3',product_type='drawer',count=3,bucket='review')],tolerances=dict(dimension_mm=0,quantity=.0001))
        actual=dict(rows=[dict(id='Q1',product_type='cabinet',length_mm=2398,quantity=6.235),
                          dict(id='Q2',product_type='wall_panel',length_mm=2602,quantity=6.765),
                          dict(id='Q3',product_type='drawer',count=3,quantity=3),dict(id='Q3',product_type='drawer')],review=[],excluded=[])
        report=compare_benchmark(expected,actual)
        self.assertEqual(report['metrics']['dimension_matches'],0)
        self.assertEqual(report['metrics']['false_confirmed'],1)
        self.assertEqual(report['metrics']['quantity_matches'],0)
        self.assertEqual(report['duplicates'],['Q3']); self.assertFalse(report['passed'])
        self.assertNotIn('accuracy',report['metrics'])
        actual['rows']=actual['rows'][:1]
        report=compare_benchmark(expected,actual)
        self.assertEqual(report['missing'],['Q2','Q3'])

    def test_benchmark_requires_nonempty_reference_and_status_pending_is_counted(self):
        self.assertIsNotNone(importlib.util.find_spec('benchmark_takeoff'),'benchmark helper is missing')
        from benchmark_takeoff import compare_benchmark
        with self.assertRaises(ValueError): compare_benchmark(dict(cases=[]),dict(rows=[]))
        report=compare_benchmark(dict(cases=[dict(id='Q1',status='待复核')]),dict(rows=[dict(id='Q1',status='依据齐全')]))
        self.assertEqual(report['metrics']['false_confirmed'],1)

    def test_benchmark_pending_and_tolerance_are_explicit(self):
        self.assertIsNotNone(importlib.util.find_spec('benchmark_takeoff'),'benchmark helper is missing')
        from benchmark_takeoff import compare_benchmark
        expected=dict(cases=[dict(id='Q1',length_mm=2400,quantity=6.24,bucket='rows'),dict(id='Q2',bucket='review')],tolerances=dict(dimension_mm=1,quantity=.01))
        actual=dict(rows=[dict(id='Q1',length_mm=2400.5,quantity=6.245)],review=[dict(id='Q2')],excluded=[])
        report=compare_benchmark(expected,actual)
        self.assertTrue(report['passed']); self.assertEqual(report['metrics']['genuine_pending'],1)
        self.assertEqual(report['tolerances']['dimension_mm'],1)


if __name__=='__main__': unittest.main()
