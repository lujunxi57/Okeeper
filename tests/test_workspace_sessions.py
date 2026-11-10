import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock,patch

import workspace_sessions as work


class WorkspaceSessionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.state=self.root/'state';self.workspace=self.root/'workspaces'
        self.workspace.mkdir();(self.workspace/'项目 alpha').mkdir();(self.workspace/'.private').mkdir()
        (self.workspace/'plain.txt').write_text('not a directory')
        self.outside=self.root/'outside';self.outside.mkdir();(self.workspace/'escape').symlink_to(self.outside)
        p=patch.dict(os.environ,{'OKEEPER_WORKSPACE_ROOTS':str(self.workspace)});p.start();self.addCleanup(p.stop)
        self.rpc=Mock();self.rpc.call.side_effect=self.call
        self.created='00000000-0000-4000-8000-000000000001'
        self.payload={'request_id':str(uuid.uuid4()),'cwd':str(self.workspace/'项目 alpha'),'name':'测试会话'}
        p=patch.object(work,'RPC',return_value=self.rpc);p.start();self.addCleanup(p.stop)
    def call(self,method,params):
        if method=='thread/start':return {'thread':{'id':self.created,'cwd':params['cwd'],'status':{'type':'idle'},'model':'gpt-6.1-sol'},'model':'gpt-6.1-sol','reasoningEffort':'high'}
        if method=='thread/name/set':return {}
        raise AssertionError(method)
    def test_browse_returns_only_accessible_folders_and_never_escapes_root(self):
        result=work.browse();self.assertEqual(result['path'],str(self.workspace));self.assertIsNone(result['parent'])
        self.assertEqual([d['name'] for d in result['directories']],['项目 alpha'])
        self.assertEqual(work.browse(str(self.workspace/'项目 alpha'))['parent'],str(self.workspace))
        for path in (self.outside,self.workspace/'escape',self.workspace/'.private',self.workspace/'plain.txt',self.workspace/'missing'):
            with self.subTest(path=path),self.assertRaises(ValueError):work.folder(str(path))
    def test_create_empty_chat_in_exact_selected_folder_and_retry_does_not_duplicate(self):
        result=work.create(self.state,self.payload)
        self.assertEqual(result['id'],self.created);self.assertEqual(result['name'],'测试会话')
        self.assertEqual(work.create(self.state,self.payload),result)
        calls=self.rpc.call.call_args_list
        self.assertEqual([c.args[0] for c in calls],['thread/start','thread/name/set'])
        self.assertEqual(calls[0].args[1],{'cwd':str(self.workspace/'项目 alpha'),'ephemeral':False})
        self.assertEqual(calls[1].args[1],{'threadId':self.created,'name':'测试会话'})
        self.rpc.close.assert_called_once()
    def test_request_lost_after_start_never_automatically_creates_again(self):
        self.rpc.call.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):work.create(self.state,self.payload)
        with self.assertRaises(ValueError):work.create(self.state,self.payload)
        self.assertEqual(self.rpc.call.call_count,1)
    def test_name_failure_retains_created_session_and_returns_warning(self):
        def call(method,params):
            if method=='thread/name/set':raise ConnectionError()
            return self.call(method,params)
        self.rpc.call.side_effect=call
        result=work.create(self.state,self.payload)
        self.assertEqual(result['id'],self.created);self.assertIn('warning',result)
        self.assertEqual(work.create(self.state,self.payload)['id'],self.created)
    def test_disabled_current_account_prevents_session_creation(self):
        with patch.object(work,'runtime_enabled',return_value=False),self.assertRaises(ValueError):work.create(self.state,self.payload)
        self.rpc.call.assert_not_called()
    def test_validate_models_and_efforts_and_preserve_default_permissions(self):
        models=[{'id':'model','efforts':['high']}]
        with patch.object(work,'list_models',return_value=models):
            result=work.create(self.state,{**self.payload,'model':'model','effort':'high'})
        self.assertEqual(result['id'],self.created)
        self.assertEqual(self.rpc.call.call_args_list[0].args[1],{'cwd':str(self.workspace/'项目 alpha'),'ephemeral':False,'model':'model','config':{'model_reasoning_effort':'high'}})
    def test_invalid_paths_and_requests_never_reach_codex(self):
        for change in ({'request_id':'bad'},{'cwd':str(self.outside)},{'name':'a\nb'},{'model':{}},{'effort':'high'},{'command':'rm'}):
            with self.subTest(change=change),self.assertRaises(ValueError):work.create(self.state,{**self.payload,**change})
        self.rpc.call.assert_not_called()
    def test_same_id_cannot_be_reused_for_different_folder_or_name(self):
        work.create(self.state,self.payload)
        with self.assertRaises(ValueError):work.create(self.state,{**self.payload,'name':'changed'})
