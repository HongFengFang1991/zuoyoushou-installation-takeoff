"""Synthetic project portability and explicit legacy migration regressions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
import zipfile


class ProjectToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.old=self.root/'old'; self.old.mkdir()
        self.source=self.root/'source.png'; self.source.write_bytes(b'synthetic-source-no-real-drawing')
        self.state=dict(runtime_version='1.2.0',revision=7,documents=[dict(id='D001',file=self.source.name,path=str(self.source),page_count=1,sha256=hashlib.sha256(self.source.read_bytes()).hexdigest())],
            rendered={'D001:1':{'image':'pages/one.png'}},crops={},batches={'b1':'hash'},takeoff=dict(schema_version=1,evidence_policy=2,project='合成',items=[dict(id='Q1',length_mm=2400,evidence=dict(basis='explicit_dimensions'))],pending_review_inputs=[dict(row_id='Q1',text='未确认')]))
        self.write_state()
        for name in ('pages/one.png','crops/local.png','changes/b1.json','exports/v1/quote.xlsx','calculation/calculated.json'):
            path=self.old/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'synthetic artifact')

    def tearDown(self): self.tmp.cleanup()

    def tools(self):
        self.assertIsNotNone(importlib.util.find_spec('project_tools'),'project helper is missing')
        import project_tools
        return project_tools

    def write_state(self): (self.old/'state.json').write_text(json.dumps(self.state,ensure_ascii=False),encoding='utf-8')

    def test_explicit_migration_snapshots_preserves_policy_and_old_directory(self):
        t=self.tools(); before=(self.old/'state.json').read_bytes(); dest=self.root/'new'
        result=t.migrate_project(self.old,dest)
        self.assertEqual((self.old/'state.json').read_bytes(),before)
        self.assertEqual(result['runtime_version'],'1.3.0'); self.assertEqual(result['takeoff']['evidence_policy'],2)
        self.assertEqual(result['takeoff']['items'],self.state['takeoff']['items'])
        self.assertEqual(result['takeoff']['revision'],7)
        self.assertEqual(result['takeoff']['project_id'],result['project_id'])
        self.assertEqual(result['takeoff']['source_fingerprint'],result['source_fingerprint'])
        self.assertEqual(len(result['source_fingerprint']),64)
        self.assertNotIn('_base_dir',result['takeoff'])
        self.assertEqual(result['documents'][0]['path'],str(self.source))
        self.source.unlink(); self.assertTrue((dest/result['documents'][0]['stored_path']).is_file())
        self.assertTrue((dest/'pages/one.png').exists())
        with self.assertRaises(ValueError): t.migrate_project(dest,self.root/'again')

    def test_missing_or_changed_legacy_source_rejects_without_destination(self):
        t=self.tools(); self.source.unlink(); dest=self.root/'new'
        with self.assertRaisesRegex(ValueError,'源|source'): t.migrate_project(self.old,dest)
        self.assertFalse(dest.exists()); self.assertTrue((self.old/'state.json').exists())
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'哈希|变化|hash'): t.migrate_project(self.old,dest)
        self.assertFalse(dest.exists())

    def test_archive_restore_moves_with_sources_and_preserves_identity(self):
        t=self.tools(); current=self.root/'current'; state=t.migrate_project(self.old,current)
        for name in ('.venv/lib/large','__pycache__/a.pyc','.runtime.lock','.pending-export/half','cache/old'):
            path=current/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'not archived')
        archive=t.archive_project(current,self.root/'project.zip')
        with zipfile.ZipFile(archive) as z:
            names=z.namelist(); self.assertIn('archive-manifest.json',names)
            self.assertTrue(all(not name.startswith(('.venv','__pycache__','.pending','cache')) for name in names))
            self.assertNotIn('.runtime.lock',names)
        shutil.rmtree(current); self.source.unlink()
        restored=self.root/'restored'; result=t.restore_archive(archive,restored)
        self.assertEqual(result['project_id'],state['project_id']); self.assertEqual(result['revision'],7)
        self.assertTrue((restored/result['documents'][0]['stored_path']).is_file())
        self.assertTrue((restored/'exports/v1/quote.xlsx').exists())
        self.assertEqual(result['takeoff']['pending_review_inputs'][0]['text'],'未确认')

    def test_existing_destination_never_overwritten(self):
        t=self.tools(); dest=self.root/'occupied'; dest.mkdir(); (dest/'keep').write_text('keep')
        with self.assertRaises(ValueError): t.migrate_project(self.old,dest)
        archive=t.archive_project(self.old,self.root/'project.zip')
        with self.assertRaises(ValueError): t.restore_archive(archive,dest)
        self.assertEqual((dest/'keep').read_text(),'keep')
        with self.assertRaises(ValueError): t.archive_project(self.old,archive)

    def test_archive_tampering_is_rejected_before_restore(self):
        t=self.tools(); archive=t.archive_project(self.old,self.root/'project.zip')
        with zipfile.ZipFile(archive) as z: entries={n:z.read(n) for n in z.namelist()}
        entries['pages/one.png']=b'corrupted'
        bad=self.root/'tampered.zip'
        with zipfile.ZipFile(bad,'w') as z:
            for name,value in entries.items(): z.writestr(name,value)
        dest=self.root/'restored'
        with self.assertRaisesRegex(ValueError,'哈希|校验|hash'): t.restore_archive(bad,dest)
        self.assertFalse(dest.exists())

    def test_zip_slip_symlink_and_unlisted_entries_rejected(self):
        t=self.tools()
        for name in ('../escape','/absolute','C:/escape','folder\\escape'):
            bad=self.root/'bad.zip'
            with zipfile.ZipFile(bad,'w') as z: z.writestr(name,b'x')
            with self.assertRaises(ValueError): t.restore_archive(bad,self.root/'restore')
        bad=self.root/'link.zip'; info=zipfile.ZipInfo('sources/link'); info.create_system=3; info.external_attr=(stat.S_IFLNK|0o777)<<16
        with zipfile.ZipFile(bad,'w') as z: z.writestr(info,'../../source.png')
        with self.assertRaises(ValueError): t.restore_archive(bad,self.root/'restore')
        (self.old/'linked').symlink_to(self.source)
        with self.assertRaisesRegex(ValueError,'符号链接|symlink'): t.archive_project(self.old,self.root/'links.zip')

    def test_stored_path_authoritative_and_traversal_rejected(self):
        t=self.tools(); current=self.root/'current'; state=t.migrate_project(self.old,current)
        stored=current/state['documents'][0]['stored_path']; stored.unlink()
        with self.assertRaisesRegex(ValueError,'源|source'): t.archive_project(current,self.root/'bad.zip')
        state['documents'][0]['stored_path']='../source.png'
        (current/'state.json').write_text(json.dumps(state),encoding='utf-8')
        with self.assertRaises(ValueError): t.archive_project(current,self.root/'bad.zip')


if __name__=='__main__': unittest.main()
