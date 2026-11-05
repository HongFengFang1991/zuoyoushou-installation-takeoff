"""Independent integration edge regressions, synthetic sources only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
import test_runtime as base
from test_measurement_v13 import evidence3


class RuntimeEdgeTests(unittest.TestCase):
    setUp=base.RuntimeTests.setUp
    tearDown=base.RuntimeTests.tearDown
    start=base.RuntimeTests.start
    batch=base.RuntimeTests.batch
    edit_cell=base.RuntimeTests.edit_cell

    def ready(self,review=False):
        s=self.start(); self.rt.render_pages(self.project); batch=self.batch(s)
        if review: batch['items'][0].update(needs_review=True,review_reason='请确认高度')
        self.rt.checkpoint(self.project,batch)
        return self.rt.state(self.project)

    def feedback(self,state):
        payload=dict(schema_version=1,kind='review_inputs',project_id=state['takeoff']['project_id'],base_revision=state['revision'],inputs=[dict(row_id='Q1',text='高度为2600')])
        self.rt.import_review(self.project,payload)
        return self.rt.load_takeoff(self.project)['pending_review_inputs'][0]['id']

    def test_id_only_patch_cannot_clear_pending_feedback(self):
        state=self.ready(); pending_id=self.feedback(state)
        before=(self.project/'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'字段|修改|修订|证据'):
            self.rt.checkpoint(self.project,dict(batch_id='no-change',reason='声称已处理',items=[dict(id='Q1')],resolved_inputs=[dict(id=pending_id,reason='高度已经正确')]))
        self.assertEqual((self.project/'state.json').read_bytes(),before)

    def test_reimport_resolved_excel_feedback_is_not_new_pending(self):
        self.ready(review=True)
        old=self.rt.export_project(self.project); self.edit_cell(old,1,'S7','高度2601')
        self.rt.export_project(self.project,previous=old)
        state=self.rt.state(self.project)
        self.assertEqual(sum(v.get('status')=='pending' for v in state['takeoff']['pending_review_inputs']),1)
        pending=state['takeoff']['pending_review_inputs'][0]
        row=copy.deepcopy(state['takeoff']['items'][0]); row.update(height_mm=2601,needs_review=False,review_reason='',user_confirmation='用户复核高度2601')
        row['evidence']=evidence3(row)
        self.rt.checkpoint(self.project,dict(batch_id='resolve-height',reason='将原文高度结构化',items=[row],resolved_inputs=[dict(id=pending['id'],reason='已修改height_mm与对应读数')]))
        self.assertFalse(any(v.get('status')=='pending' for v in self.rt.load_takeoff(self.project)['pending_review_inputs']))
        self.rt.export_project(self.project,previous=old)
        final=self.rt.load_takeoff(self.project)['pending_review_inputs']
        self.assertFalse(any(v.get('status')=='pending' for v in final),'same old workbook input must remain resolved')
        self.assertEqual(len(final),1)

    def test_query_healthy_page_ignores_unrelated_damaged_index(self):
        from PIL import Image
        other=self.root/'other.png'; Image.new('RGB',(300,200),'white').save(other)
        self.rt.initialize(self.project,[self.src,other],'两张合成图')
        self.rt.render_pages(self.project); s=self.rt.state(self.project)
        (self.project/s['rendered']['D002:1']['index']).write_bytes(b'corrupted unrelated cache')
        result=self.rt.query_page(self.project,'D001',1)
        self.assertEqual(result['file'],self.src.name)

    def test_bad_source_row_stays_review_without_breaking_export_or_full_status(self):
        s=self.start(); self.rt.render_pages(self.project); batch=self.batch(s)
        bad=copy.deepcopy(batch['items'][0]); bad.update(id='Q2',instance_key='Q2',sources=None)
        batch['items'].append(bad)
        self.rt.checkpoint(self.project,batch)
        status=self.rt.status(self.project,full=True)
        self.assertEqual(status['review'],1)
        out=self.rt.export_project(self.project)
        self.assertTrue(out.exists())

    def test_migrated_rendered_unread_page_can_resume_without_old_index(self):
        from project_tools import migrate_project
        self.start(); self.rt.render_pages(self.project)
        old=self.rt.state(self.project)
        old['runtime_version']='1.2.0'; old['takeoff']['evidence_policy']=2
        for record in old['rendered'].values():
            record.pop('index',None); record.pop('index_sha256',None)
        self.rt.atomic_json(self.project/'state.json',old)
        dest=self.root/'migrated'
        migrate_project(self.project,dest)
        packet=self.rt.next_batch(dest)
        self.assertEqual(packet['remaining_pages'],1)
        self.assertEqual(packet['pages'][0]['page'],1)

    def test_readonly_status_on_missing_project_does_not_create_directory(self):
        missing=self.root/'missing-project'
        script=Path(self.rt.__file__)
        result=subprocess.run([sys.executable,str(script),'--project-dir',str(missing),'status'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(missing.exists(),'read-only status must not create a project directory')


if __name__=='__main__': unittest.main()

class MalformedRowsStayReviewTests(unittest.TestCase):
    setUp=base.RuntimeTests.setUp
    tearDown=base.RuntimeTests.tearDown
    start=base.RuntimeTests.start
    batch=base.RuntimeTests.batch
    # New end-to-end case: structured validation must also survive display/export.
    def test_bad_nested_payload_can_be_exported_as_review(self):
        from unittest.mock import patch
        state=self.start(); self.rt.render_pages(self.project)
        batch=self.batch(state)
        for number, update in enumerate(({'sources':17},{'sources':'bad'}, {'evidence':[]}, {'evidence':'bad'}, {'evidence':None}),2):
            row=copy.deepcopy(batch['items'][0]); row.update(id=f'Q{number}',instance_key=f'Q{number}',**update)
            batch['items'].append(row)
        self.rt.checkpoint(self.project,batch)
        self.assertEqual(self.rt.status(self.project,full=True)['review'],5)
        self.rt.audit_project(self.project)
        self.assertTrue(self.rt.export_project(self.project).exists())
