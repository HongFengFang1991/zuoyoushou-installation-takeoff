"""End-to-end state, resume and efficiency regressions using synthetic source only."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import test_runtime as base

class IntegrationV13Tests(unittest.TestCase):
    setUp=base.RuntimeTests.setUp
    tearDown=base.RuntimeTests.tearDown
    start=base.RuntimeTests.start
    batch=base.RuntimeTests.batch
    edit_cell=base.RuntimeTests.edit_cell
    def test_source_snapshot_survives_original_move(self):
        s=self.start(); self.src.unlink()
        self.rt.render_pages(self.project)
        self.assertEqual(s['takeoff']['evidence_policy'],3)
        self.assertTrue(s['takeoff']['project_id'])
        self.assertEqual(len(s['takeoff']['source_fingerprint']),64)

    def test_next_is_bounded_and_index_cached(self):
        s=self.start(); self.rt.render_pages(self.project)
        packet=self.rt.next_batch(self.project,limit=1)
        self.assertEqual(len(packet['pages']),1)
        r=next(iter(self.rt.state(self.project)['rendered'].values()))
        stamp=(self.project/r['index']).stat().st_mtime_ns
        self.rt.render_pages(self.project)
        self.assertEqual(stamp,(self.project/r['index']).stat().st_mtime_ns)
        self.assertLess(len(json.dumps(packet)),6000)

    def test_manual_price_persisted_without_previous_next_export(self):
        s=self.start(); self.rt.render_pages(self.project); self.rt.checkpoint(self.project,self.batch(s))
        path=self.rt.export_project(self.project); self.edit_cell(path,2,'D7',35)
        self.rt.export_project(self.project,previous=path)
        self.assertEqual(self.rt.load_takeoff(self.project)['items'][0]['unit_price'],35)
        from export_quote import read_cells
        out=self.rt.export_project(self.project)
        self.assertEqual(read_cells(out)['报价明细']['J7']['value'],218.4)

    def test_review_input_is_saved_and_never_auto_confirms(self):
        s=self.start(); self.rt.render_pages(self.project); b=self.batch(s)
        b['items'][0].update(needs_review=True,review_reason='需确认总高')
        self.rt.checkpoint(self.project,b)
        s=self.rt.state(self.project)
        payload=dict(schema_version=1,project_id=s['takeoff']['project_id'],base_revision=s['revision'],kind='review_inputs',inputs=[dict(row_id='Q1',text='总高2600')])
        self.rt.import_review(self.project,payload)
        t=self.rt.load_takeoff(self.project)
        self.assertEqual(t['pending_review_inputs'][0]['text'],'总高2600')
        self.assertTrue(t['items'][0]['needs_review'])
        with self.assertRaises(ValueError): self.rt.import_review(self.project,dict(payload,project_id='foreign'))
        self.assertEqual(self.rt.import_review(self.project,payload)['reused'],True)

    def test_prices_and_rules_never_render_source(self):
        s=self.start(); self.rt.render_pages(self.project); self.rt.checkpoint(self.project,self.batch(s))
        with patch('takeoff_runtime.render_source',side_effect=AssertionError('must reuse images')):
            self.rt.checkpoint(self.project,dict(batch_id='price2',reason='用户报价35',items=[dict(id='Q1',unit_price=35,price_unit='平米',price_note='用户35')]))
            self.rt.export_project(self.project)

    def test_export_failure_does_not_lose_accepted_manual_changes(self):
        s=self.start(); self.rt.render_pages(self.project); self.rt.checkpoint(self.project,self.batch(s))
        out=self.rt.export_project(self.project); self.edit_cell(out,2,'D7',35)
        with patch('export_quote.export_workbook',side_effect=PermissionError('test')):
            with self.assertRaises(PermissionError): self.rt.export_project(self.project,previous=out)
        self.assertEqual(self.rt.load_takeoff(self.project)['items'][0]['unit_price'],35)
        self.assertTrue(self.rt.export_project(self.project).exists())

    def test_profile_save_preview_apply_and_idempotence(self):
        s=self.start(); self.rt.render_pages(self.project); self.rt.checkpoint(self.project,self.batch(s))
        profile=dict(schema_version=1,kind='prices',profile_id='provider1',name='用户档案',version='1',source_text='用户35元每平米',prices=[dict(id='price1',selector={'product_type':'cabinet'},method='projection_area',unit='平米',unit_price=35)])
        draft=self.root/'draft.json'; self.rt.atomic_json(draft,profile)
        target=self.root/'saved.json'
        self.rt.profile_action(self.project,draft,'save',target)
        self.assertEqual(self.rt.read_json(target),profile)
        self.assertEqual(len(self.rt.profile_action(self.project,target,'preview')['matched']),1)
        self.rt.profile_action(self.project,target,'apply')
        rev=self.rt.state(self.project)['revision']
        self.assertTrue(self.rt.profile_action(self.project,target,'apply')['reused'])
        self.assertEqual(self.rt.state(self.project)['revision'],rev)
        self.assertEqual(self.rt.load_takeoff(self.project)['items'][0]['unit_price'],35)

    def test_scoped_user_confirmation_preserves_original_reading(self):
        s=self.start(); self.rt.render_pages(self.project); self.rt.checkpoint(self.project,self.batch(s))
        row=copy.deepcopy(self.rt.load_takeoff(self.project)['items'][0])
        row['height_mm']=2500; row['user_confirmation']='用户：现场高度2500'
        row['evidence']['basis']='user_confirmed'
        row['evidence']['field_confirmations']=[dict(field='height_mm',value=2500,reason='现场复尺',user_confirmation='用户：现场高度2500')]
        self.rt.checkpoint(self.project,dict(batch_id='height2',reason=row['user_confirmation'],items=[row]))
        calc=self.rt.audit_project(self.project)
        self.assertEqual(calc['confirmed'],1)
        row=self.rt.load_takeoff(self.project)['items'][0]
        self.assertEqual(row['evidence']['dimension_readings'][1]['value_mm'],2600)
        self.assertEqual(row['height_mm'],2500)
