import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

import account_continue as continuation
import quota_watch
from account_quota import atomic_json


SOURCE=('workspace-a','user-a','alice@example.com')
TARGET=('workspace-b','user-b','bob@example.com')


def thread(id='chat',**fields):
    return {'id':id,'source':'vscode','modelProvider':'openai','status':{'type':'idle'},
            'model':'gpt-6.1-sol','reasoningEffort':'high','updatedAt':time.time(),**fields}


def turn(id='source-turn',status='failed',error='usageLimitExceeded',**fields):
    return {'id':id,'status':status,'error':{'codexErrorInfo':error} if error else None,
            'completedAt':time.time()*1000,**fields}


class FakeRPC:
    def __init__(self,key=TARGET,threads=None,turns=None):
        self.key=key;self.threads=threads or {'chat':thread()};self.turns=turns or {'chat':turn()}
        self.calls=[];self.closed=False;self.start_error=None;self.on_resume=None;self.on_start=None;self.on_read=None
    def call(self,method,params):
        self.calls.append((method,copy.deepcopy(params)))
        id=params.get('threadId')
        if method=='account/read':return {'account':{'type':'chatgpt','email':self.key[2]},'workspaceRouting':{'chatgptAccountId':self.key[0]}}
        if method=='thread/list':return {'data':list(self.threads.values()),'nextCursor':None}
        if method=='thread/loaded/list':return {'data':list(self.threads)}
        if method=='thread/read':
            if self.on_read:self.on_read(self,id)
            return {'thread':copy.deepcopy(self.threads[id])}
        if method=='thread/turns/list':return {'data':[copy.deepcopy(self.turns[id])],'nextCursor':None}
        if method=='thread/resume':
            if self.on_resume:self.on_resume(self,id)
            return {'thread':copy.deepcopy(self.threads[id]),'model':self.threads[id]['model'],'reasoningEffort':self.threads[id]['reasoningEffort']}
        if method=='turn/start':
            if self.on_start:self.on_start(self,id)
            if self.start_error:raise self.start_error
            return {'turn':{'id':'continued-'+id,'status':'inProgress'}}
        raise AssertionError(method)
    def close(self):self.closed=True
    def starts(self):return [params for method,params in self.calls if method=='turn/start']


class ContinueTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(temporary.cleanup)
        self.state=Path(temporary.name);self.home=self.state/'home';self.home.mkdir()
        self.setting=quota_watch.configure(self.state,{'enabled':True,'continue_tasks':True})
        self.candidate={'thread_id':'chat','source_turn_id':'source-turn','reason':'quota_failed'}
        self.plan={'candidates':[self.candidate]}
        p=patch.object(continuation,'managed_ids',return_value=set());p.start();self.addCleanup(p.stop)
        p=patch.object(continuation,'PREPARE_WAIT_SECONDS',0);p.start();self.addCleanup(p.stop)
    def resume(self,rpc,plan=None):
        with patch.object(continuation,'RPC',return_value=rpc):
            return continuation.resume(self.state,self.home,TARGET,self.setting['generation'],plan or self.plan,'operation')
    def capture(self,rpc):
        with patch.object(continuation,'RPC',return_value=rpc):return continuation.capture(self.state,self.home,SOURCE)

    def test_capture_only_recent_quota_failures_and_active_root_tasks(self):
        ids=['recent','old','completed','generic_error','active','waiting','subagent','managed','ephemeral','archived','other_provider']
        threads={id:thread(id) for id in ids};turns={id:turn(id+'-turn') for id in ids}
        turns['old']=turn('old-turn',completedAt=(time.time()-3600)*1000)
        turns['completed']=turn('completed-turn','completed',None)
        turns['generic_error']=turn('generic-turn',error='HttpConnectionFailed')
        for id in ('active','waiting'):
            threads[id]['status']={'type':'active','activeFlags':[]};turns[id]=turn(id+'-turn','inProgress',None)
        threads['waiting']['status']['activeFlags']=['waitingOnApproval']
        threads['subagent']['parentThreadId']='parent'
        threads['ephemeral']['ephemeral']=True
        threads['archived']['path']='/private/archived_sessions/chat.jsonl'
        threads['other_provider']['modelProvider']='external'
        rpc=FakeRPC(SOURCE,threads,turns)
        with patch.object(continuation,'managed_ids',return_value={'managed'}):result=self.capture(rpc)
        self.assertEqual({c['thread_id'] for c in result['candidates']},{'recent','active'})
        self.assertEqual(rpc.starts(),[]);self.assertTrue(rpc.closed)
        self.assertNotIn('prompt',json.dumps(result))

    def test_capture_rejects_wrong_source_account(self):
        rpc=FakeRPC(TARGET)
        with self.assertRaises(RuntimeError):self.capture(rpc)
        self.assertEqual([method for method,_ in rpc.calls],['account/read'])

    def test_continue_in_original_thread_without_execution_setting_overrides(self):
        rpc=FakeRPC();result=self.resume(rpc)
        self.assertEqual(result['accepted'],1)
        start=rpc.starts()[0]
        self.assertEqual(start['threadId'],'chat');self.assertEqual(start['input'][0]['text'],'continue')
        self.assertEqual(set(start),{'threadId','input','clientUserMessageId'})
        self.assertEqual([p for m,p in rpc.calls if m=='thread/resume'],[{'threadId':'chat','excludeTurns':True}])
        self.assertEqual(self.resume(rpc)['skipped'],1);self.assertEqual(len(rpc.starts()),1)
        self.assertTrue(rpc.closed)

    def test_receipt_is_written_before_send_and_uncertain_response_is_never_replayed(self):
        rpc=FakeRPC();rpc.start_error=TimeoutError()
        def verify_claim(client,id):
            receipt=json.loads(continuation.receipt_path(self.state,self.candidate).read_text())
            self.assertEqual(receipt['status'],'sending')
        rpc.on_start=verify_claim
        result=self.resume(rpc);self.assertEqual(result['unconfirmed'],1)
        self.assertEqual(self.resume(rpc)['skipped'],1);self.assertEqual(len(rpc.starts()),1)

    def test_closed_option_or_new_generation_prevents_send(self):
        for payload in ({'continue_tasks':False},{'continue_tasks':True},{'enabled':False}):
            quota_watch.configure(self.state,payload)
            rpc=FakeRPC();self.assertEqual(self.resume(rpc)['accepted'],0);self.assertEqual(rpc.calls,[])

    def test_target_identity_is_checked_before_resuming(self):
        rpc=FakeRPC(SOURCE);self.assertEqual(self.resume(rpc)['accepted'],0)
        self.assertEqual([m for m,_ in rpc.calls],['account/read'])

    def test_completed_or_user_continued_or_busy_chats_are_skipped(self):
        for status in ('completed','interrupted','new_turn','active','waiting'):
            with self.subTest(status=status):
                rpc=FakeRPC()
                if status=='new_turn':rpc.turns['chat']=turn('newer-turn')
                elif status in ('active','waiting'):rpc.threads['chat']['status']={'type':'active','activeFlags':['waitingOnUserInput'] if status=='waiting' else []}
                else:rpc.turns['chat']=turn(status=status,error=None)
                self.assertEqual(self.resume(rpc)['accepted'],0);self.assertEqual(rpc.starts(),[])

    def test_only_snapshot_active_task_can_continue_restart_interruption(self):
        self.candidate['reason']='active_before_switch';rpc=FakeRPC(turns={'chat':turn(status='interrupted',error=None)})
        self.assertEqual(self.resume(rpc)['accepted'],1)

    def test_changed_turn_or_settings_during_resume_prevents_send(self):
        rpc=FakeRPC();rpc.on_resume=lambda client,id:client.turns.update({id:turn('manual-turn','inProgress',None)})
        self.assertEqual(self.resume(rpc)['accepted'],0);self.assertEqual(rpc.starts(),[])
        rpc=FakeRPC()
        def change_settings(client,id):
            client.threads[id]['model']='other-model'
        reads=0
        def on_read(client,id):
            nonlocal reads
            reads+=1
            if reads==2:change_settings(client,id)
        rpc.on_read=on_read
        self.assertEqual(self.resume(rpc)['accepted'],0);self.assertEqual(rpc.starts(),[])

    def test_disable_during_resume_or_account_change_before_send_prevents_send(self):
        rpc=FakeRPC();rpc.on_resume=lambda *_:quota_watch.configure(self.state,{'continue_tasks':False})
        self.assertEqual(self.resume(rpc)['accepted'],0);self.assertEqual(rpc.starts(),[])
        self.setting=quota_watch.configure(self.state,{'continue_tasks':True})
        rpc=FakeRPC();rpc.on_resume=lambda client,id:setattr(client,'key',SOURCE)
        self.assertEqual(self.resume(rpc)['accepted'],0);self.assertEqual(rpc.starts(),[])

    def test_one_busy_chat_does_not_block_other_affected_chats(self):
        rpc=FakeRPC(threads={'chat':thread(),'busy':thread('busy',status={'type':'active'})},turns={'chat':turn(),'busy':turn('busy-turn')})
        plan={'candidates':[{'thread_id':'busy','source_turn_id':'busy-turn','reason':'quota_failed'},self.candidate]}
        result=self.resume(rpc,plan)
        self.assertEqual((result['accepted'],result['skipped']),(1,1))

    def test_capture_deadline_is_reported_and_never_sends(self):
        rpc=FakeRPC(SOURCE)
        with patch.object(continuation.time,'monotonic',side_effect=[0,20]):result=self.capture(rpc)
        self.assertTrue(result['incomplete']);self.assertEqual(result['candidates'],[]);self.assertEqual(rpc.starts(),[])

    def test_generic_error_or_missing_settings_never_sends(self):
        rpc=FakeRPC(turns={'chat':turn(error='HttpConnectionFailed')})
        self.assertEqual(self.resume(rpc)['accepted'],0)
        rpc=FakeRPC();rpc.threads['chat']['reasoningEffort']=None
        self.assertEqual(self.resume(rpc)['accepted'],0);self.assertEqual(rpc.starts(),[])

    def test_waits_for_shutdown_to_finish_without_interrupting_any_turn(self):
        rpc=FakeRPC();rpc.threads['chat']['status']={'type':'active','activeFlags':[]}
        reads=0
        def settle(client,id):
            nonlocal reads
            reads+=1
            if reads>=2:client.threads[id]['status']={'type':'idle'}
        rpc.on_read=settle
        with patch.object(continuation,'PREPARE_WAIT_SECONDS',1),patch.object(continuation.time,'sleep'):
            self.assertEqual(self.resume(rpc)['accepted'],1)
        self.assertFalse(any(method=='turn/interrupt' for method,_ in rpc.calls))

    def test_both_wire_case_variants_supported_but_generic_429_is_not(self):
        self.assertTrue(continuation.quota_failure(turn(error='UsageLimitExceeded')))
        self.assertTrue(continuation.quota_failure(turn()))
        self.assertFalse(continuation.quota_failure(turn(error={'httpConnectionFailed':{'httpStatusCode':429}})))

    def test_migration_keeps_automatic_switch_and_defaults_continue_off(self):
        atomic_json(self.state/'quota-watch-config.json',{'enabled':True,'generation':'legacy'})
        value=quota_watch.public_state(self.state)
        self.assertTrue(value['enabled']);self.assertFalse(value['continue_tasks']);self.assertEqual(value['generation'],'legacy')
        value=quota_watch.configure(self.state,{'continue_tasks':True})
        self.assertTrue(value['enabled']);self.assertTrue(value['continue_tasks']);self.assertNotEqual(value['generation'],'legacy')
        with self.assertRaises(ValueError):quota_watch.configure(self.state,{'continue_tasks':1})
