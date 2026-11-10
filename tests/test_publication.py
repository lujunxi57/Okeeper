"""Check the upload boundary against a disposable Git index, never live state."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        (self.root/'scripts').mkdir()
        source=Path(__file__).resolve().parents[1]/'scripts/check_publication.py'
        shutil.copy2(source,self.root/'scripts/check_publication.py')
        self.git('init','-q')

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.STDOUT)

    def stage(self,name,value):
        path=self.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(value)
        self.git('add','--',name)

    def check(self):
        return subprocess.run([sys.executable,str(self.root/'scripts/check_publication.py')],
                              cwd=self.root,capture_output=True,text=True)

    def test_permits_source_and_the_named_static_icon(self):
        self.stage('app/example.py',b'print("ok")\n')
        self.stage('web/icon.png',b'\x89PNG\r\n\x1a\n')
        self.assertEqual(self.check().returncode,0)

    def test_rejects_runtime_files_and_unreviewed_images(self):
        for name in ('state/runs.sqlite','artifacts/result.json','web/screenshot.png','.env.local'):
            self.stage(name,b'private runtime data')
        result=self.check()
        self.assertNotEqual(result.returncode,0)
        for name in ('state/runs.sqlite','artifacts/result.json','web/screenshot.png','.env.local'):
            self.assertIn(name,result.stdout)

    def test_credential_in_staged_blob_is_rejected_even_if_working_copy_is_clean(self):
        fake_key=b'sk-'+b'x'*40
        self.stage('app/example.py',fake_key)
        (self.root/'app/example.py').write_bytes(b'print("ok")\n')
        result=self.check()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('possible credential',result.stdout)
        self.assertNotIn(fake_key.decode(),result.stdout)

    def test_unstaged_data_is_not_mistaken_for_upload_content(self):
        self.stage('app/example.py',b'print("ok")\n')
        (self.root/'app/example.py').write_bytes(b'sk-'+b'x'*40)
        self.assertEqual(self.check().returncode,0)

    def test_static_icon_exception_rejects_non_png_content(self):
        self.stage('web/icon.png',b'not an icon')
        result=self.check()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('invalid static PNG asset',result.stdout)

    def test_rejects_staged_symlinks_even_if_working_copy_is_replaced(self):
        path=self.root/'link.py'
        path.symlink_to('elsewhere')
        self.git('add','link.py')
        path.unlink();path.write_text('print("ok")\n')
        result=self.check()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('symlink requires manual boundary review',result.stdout)
