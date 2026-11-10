"""Execute selected-account tasks against disposable Homes and a fake daemon."""
import datetime as dt
import fcntl
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import account_control as control
import account_preferences as preferences
import custom_account as ca
import custom_store as store
import custom_worker as worker
import request_history
import schedule_store as schedule
from auth_profiles import profiles,read_auth
from test_custom_alarms import FakeRPC as BaseRPC,A,B
from test_multi_account_quota import auth


class CustomAccountTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.state=Path(temp.name)/'state';self.home=Path(temp.name)/'home'
        (self.home/'auth-profiles').mkdir(parents=True)
        for user in ('alice','bob'):
            (self.home/'auth-profiles'/f'account-{user}.json').write_text(json.dumps(auth(user)))
        (self.home/'auth.json').write_text(json.dumps(auth('alice')))
        self.accounts={p['key'][2]:p for p in profiles(self.home).values()}
        self.bob=self.accounts['bob@example.com']['id']
        self.alice=self.accounts['alice@example.com']['id']
        self.clock=dt.datetime(2026,10,24,7,0,1,tzinfo=schedule.TZ)
        self.calls=[];self.rpcs=[];self.busy_other=False;self.busy_target=False
        self.page_incomplete=False;self.scan_count=0;self.on_scan=None
        self.switch_seconds=0;self.switch_fails=False;self.lose_send_response=False
        self.wrong_runtime=False;self.native_effort='high'
        original_connection=ca.connection;original_verify=ca.verify_target
        patches=[patch.object(module,'now',lambda:self.clock) for module in (schedule,store,worker,ca)]
        patches += [patch.object(worker,'STATE',self.state),patch.object(worker,'RPC',self.rpc),
                    patch.object(worker,'log',lambda *a,**kw:None),
                    patch.object(ca,'connection',lambda run,state,**kw:original_connection(run,state,home=self.home,**kw)),
                    patch.object(ca,'verify_target',lambda rpc,run,state,home=None:original_verify(rpc,run,state,self.home)),
                    patch.object(control,'daemon',self.daemon),patch.object(control,'verify',self.verify),
                    patch.object(control,'refreshed_native',lambda p:p)]
        for item in patches:item.start();self.addCleanup(item.stop)

    def rpc(self):
        rpc=AccountRPC(self);self.rpcs.append(rpc);return rpc

    def daemon(self,action,home):
        self.calls.append((action,read_auth(home/'auth.json')['key'][2]))
        if action=='start':
            self.clock+=dt.timedelta(seconds=self.switch_seconds)
            if self.switch_fails and read_auth(home/'auth.json')['id']==self.bob:
                raise RuntimeError('fake target launch failure')

    def verify(self,home,key):
        self.assertEqual(read_auth(home/'auth.json')['key'],key)
        self.calls.append(('verify',key[2]))
        return {'running':True,'email':key[2],'workspace':key[0]}

    def reserve(self,account=None,mode='daily'):
        account=self.bob if account is None else account
        id=schedule.create_alarm(self.state,{'time':'07:00','target_type':'thread','thread_id':A,
            'prompt':'  第一行\n第二行  ','account_id':account,'mode':mode},account_name='bob@example.com',thread_name='研究会话')
        alarm=next(a for a in schedule.list_alarms(self.state) if a['id']==id)
        return id,store.reserve(self.state,alarm,self.clock.replace(second=0))

    def row(self,slot):
        with schedule.database(self.state) as db:return dict(db.execute('SELECT * FROM custom_runs WHERE slot=?',(slot,)).fetchone())

    def sent(self):return [(method,params) for method,params in self.calls if method=='turn/start']

    def test_switch_idle_backend_then_send_original_chat_once_with_inherited_settings(self):
        _,slot=self.reserve();self.switch_seconds=45
        worker.execute(slot);worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'completed')
        self.assertEqual(read_auth(self.home/'auth.json')['id'],self.bob)
        self.assertEqual([m for m,p in self.calls if m in ('stop','start')],['stop','start'])
        self.assertLess(next(i for i,c in enumerate(self.calls) if c==('verify','bob@example.com')),
                        next(i for i,c in enumerate(self.calls) if c[0]=='turn/start'))
        self.assertEqual(len(self.sent()),1)
        params=self.sent()[0][1];self.assertEqual(params['threadId'],A)
        self.assertEqual(set(params),{'threadId','input','clientUserMessageId'})
        row=self.row(slot);self.assertEqual((row['account_id'],row['account_name'],row['route']),(self.bob,'bob@example.com','main'))
        self.assertTrue(all(rpc.closed for rpc in self.rpcs))

    def test_matching_account_and_follow_main_never_restart(self):
        for account in (self.alice,''):
            with self.subTest(account=account):
                id,slot=self.reserve(account);worker.execute(slot)
                self.assertEqual(self.row(slot)['status'],'completed')
                self.assertFalse(any(m=='stop' for m,p in self.calls))
                schedule.delete_alarm(self.state,id)
                self.clock+=dt.timedelta(days=1)

    def test_switch_then_explicit_low_effort_on_empty_chat_submits_once(self):
        id,_=self.reserve();schedule.update_alarm(self.state,id,{'custom_model':'gpt-5.6-luna','custom_effort':'low'})
        # The original claim is obsolete after editing; use a new occurrence.
        self.clock+=dt.timedelta(days=1)
        alarm=next(a for a in schedule.list_alarms(self.state) if a['id']==id)
        slot=store.reserve(self.state,alarm,self.clock.replace(second=0));self.native_effort=None
        worker.execute(slot);worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'completed')
        self.assertEqual(read_auth(self.home/'auth.json')['id'],self.bob)
        self.assertEqual(len(self.sent()),1)
        self.assertEqual((self.sent()[0][1]['model'],self.sent()[0][1]['effort']),('gpt-5.6-luna','low'))

    def test_deferred_creation_happens_after_switch_and_uses_selected_folder(self):
        id,_=self.reserve()
        with patch('workspace_sessions.roots',return_value=[self.state]):
            schedule.update_alarm(self.state,id,{'new_session':{'cwd':str(self.state),'name':'新实验'},'custom_model':'gpt-5.6-luna','custom_effort':'low'})
        self.clock+=dt.timedelta(days=1)
        alarm=next(a for a in schedule.list_alarms(self.state) if a['id']==id)
        slot=store.reserve(self.state,alarm,self.clock.replace(second=0))
        self.native_effort=None
        original=AccountRPC.call
        def call(rpc,method,params):
            if method=='thread/start':
                self.calls.append((method,params))
                self.assertEqual(read_auth(self.home/'auth.json')['id'],self.bob)
                return {'thread':rpc.thread(),'model':'gpt-5.6-luna','reasoningEffort':'low'}
            if method=='thread/name/set':self.calls.append((method,params));return {}
            return original(rpc,method,params)
        with patch.object(AccountRPC,'call',call),patch('workspace_sessions.roots',return_value=[self.state]):worker.execute(slot);worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'completed')
        self.assertEqual(len(self.sent()),1)
        creation=next(p for m,p in self.calls if m=='thread/start')
        self.assertEqual(creation,{'cwd':str(self.state),'ephemeral':False,'model':'gpt-5.6-luna','config':{'model_reasoning_effort':'low'}})
        methods=[m for m,p in self.calls]
        self.assertLess(methods.index('verify'),methods.index('thread/start'))
        self.assertEqual(methods.count('thread/start'),1)

    def test_busy_other_or_target_and_incomplete_inventory_prevent_switch(self):
        for field in ('busy_other','busy_target','page_incomplete'):
            with self.subTest(field=field):
                setattr(self,field,True);id,slot=self.reserve();worker.execute(slot)
                self.assertEqual(self.row(slot)['status'],'skipped_busy')
                self.assertEqual(read_auth(self.home/'auth.json')['id'],self.alice)
                self.assertFalse(self.sent());self.assertFalse(any(m=='stop' for m,p in self.calls))
                schedule.delete_alarm(self.state,id);setattr(self,field,False);self.clock+=dt.timedelta(days=1)

    def test_idle_rechecked_immediately_before_stop(self):
        _,slot=self.reserve()
        def busy():
            if self.scan_count==2:self.busy_other=True
        self.on_scan=busy;worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'skipped_switch_failed')
        self.assertEqual(control.load(self.state)['status'],'cancelled')
        self.assertFalse(any(m=='stop' for m,p in self.calls));self.assertFalse(self.sent())

    def test_edit_after_scan_cancels_switch_and_does_not_send_old_prompt(self):
        id,slot=self.reserve()
        def edit():
            if self.scan_count==1:schedule.update_alarm(self.state,id,{'prompt':'changed'})
        self.on_scan=edit;worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'skipped_changed')
        self.assertFalse(any(m=='stop' for m,p in self.calls));self.assertFalse(self.sent())

    def test_disabled_or_missing_login_never_sends(self):
        id,slot=self.reserve();preferences.configure(self.bob,{'enabled':False},self.state)
        worker.execute(slot);self.assertEqual(self.row(slot)['status'],'skipped_disabled')
        self.assertFalse(self.rpcs)
        preferences.configure(self.bob,{'enabled':True},self.state)
        schedule.delete_alarm(self.state,id);self.clock+=dt.timedelta(days=1)
        _,slot=self.reserve();(self.home/'auth-profiles/account-bob.json').unlink()
        worker.execute(slot);self.assertEqual(self.row(slot)['status'],'skipped_account_unavailable')
        self.assertFalse(self.sent());self.assertFalse(any(m=='stop' for m,p in self.calls))

    def test_failed_switch_restores_original_and_never_falls_back_to_it(self):
        _,slot=self.reserve();self.switch_fails=True;worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'skipped_switch_failed')
        self.assertEqual(read_auth(self.home/'auth.json')['id'],self.alice)
        self.assertEqual(control.load(self.state)['status'],'failed');self.assertFalse(self.sent())

    def test_runtime_identity_checked_again_after_successful_control_receipt(self):
        _,slot=self.reserve();self.wrong_runtime=True;worker.execute(slot)
        self.assertEqual(control.load(self.state)['status'],'completed')
        self.assertEqual(self.row(slot)['status'],'skipped_account_changed');self.assertFalse(self.sent())

    def test_switch_time_budget_does_not_allow_late_dispatch_or_late_send(self):
        id,slot=self.reserve();self.clock+=dt.timedelta(seconds=20);worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'skipped_missed');self.assertFalse(self.rpcs)
        schedule.delete_alarm(self.state,id);self.clock=self.clock.replace(second=1)+dt.timedelta(days=1)
        _,slot=self.reserve();self.switch_seconds=181;worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'skipped_missed');self.assertFalse(self.sent())

    def test_no_extra_lateness_budget_when_account_already_matches(self):
        _,slot=self.reserve(self.alice)
        original=worker.prepare
        def slow(*args):
            result=original(*args);self.clock+=dt.timedelta(seconds=20);return result
        with patch.object(worker,'prepare',slow):worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'skipped_missed');self.assertFalse(self.sent())

    def test_lost_submission_response_never_restarts_or_submits_twice(self):
        _,slot=self.reserve();self.lose_send_response=True
        worker.execute(slot);worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'failed_or_uncertain')
        self.assertEqual(len(self.sent()),1)
        self.assertEqual(len([c for c in self.calls if c[0]=='stop']),1)

    def test_same_chat_and_time_with_different_account_rejected(self):
        id,_=self.reserve()
        with self.assertRaises(sqlite3.IntegrityError):self.reserve(self.alice)
        schedule.update_alarm(self.state,id,{'target_type':'default','time':'08:00'})
        alarm=next(a for a in schedule.list_alarms(self.state) if a['id']==id)
        self.assertEqual(alarm['account_id'],'')

    def test_other_accepted_request_blocks_switch_even_if_runtime_still_idle(self):
        _,slot=self.reserve()
        with schedule.database(self.state) as db:
            db.execute("INSERT INTO custom_runs(slot,alarm_id,revision,thread_id,scheduled,prompt,status,at) VALUES('other','other',0,?,?,?,'accepted',?)",(B,self.clock.isoformat(),'x',self.clock.isoformat()))
        worker.execute(slot)
        self.assertEqual(self.row(slot)['status'],'skipped_busy');self.assertFalse(self.sent())

    def test_same_account_sends_can_share_lock_but_switch_cannot_interrupt_them(self):
        id,slot=self.reserve(self.alice)
        with (self.state/'account-control.lock').open('a') as other:
            fcntl.flock(other,fcntl.LOCK_SH|fcntl.LOCK_NB)
            worker.execute(slot)
            self.assertEqual(self.row(slot)['status'],'completed')
            schedule.delete_alarm(self.state,id);self.clock+=dt.timedelta(days=1)
            _,slot=self.reserve();worker.execute(slot)
            self.assertEqual(self.row(slot)['status'],'skipped_busy')
        self.assertFalse(any(m=='stop' for m,p in self.calls))

    def test_history_explains_switch_progress_and_failure(self):
        current=self.clock.timestamp()
        state,label,_=request_history.outcome('switching','custom',self.clock.isoformat(),current)
        self.assertEqual((state,label),('pending','正在切号'))
        self.assertIn('切换未完成',request_history.outcome('skipped_switch_failed','custom',self.clock.isoformat(),current)[2])


class AccountRPC(BaseRPC):
    def __init__(self,test):
        super().__init__(test,busy=test.busy_target,effort=test.native_effort);self.closed=False
        self.key=read_auth(test.home/'auth.json')['key']
        if test.wrong_runtime and self.key[2]=='bob@example.com':self.key=('alice','alice','alice@example.com')

    def call(self,method,params):
        self.test.calls.append((method,params))
        if method=='account/read':return {'account':{'type':'chatgpt','email':self.key[2]},'workspaceRouting':{'chatgptAccountId':self.key[0]}}
        if method=='thread/loaded/list':
            self.test.scan_count+=1
            if self.test.on_scan:self.test.on_scan()
            return {'data':[A,B],'nextCursor':'unknown' if self.test.page_incomplete else None}
        if method=='thread/read' and params['threadId']==B:
            return {'thread':{'id':B,'status':{'type':'active' if self.test.busy_other else 'idle','activeFlags':['waitingOnApproval'] if self.test.busy_other else []}}}
        if method=='turn/start' and self.test.lose_send_response:raise ConnectionError('response lost')
        return super().call(method,params)

    def close(self):self.closed=True


if __name__=='__main__':unittest.main()
