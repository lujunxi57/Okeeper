import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import schedule_store as s
import custom_store as c
import custom_worker as w
import custom_dispatch as d
import session_catalog as catalog

A='00000000-0000-4000-8000-000000000001'
B='00000000-0000-4000-8000-000000000002'

class CustomTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.state=Path(self.temp.name)
        self.clock=dt.datetime(2026,10,16,7,0,1,tzinfo=s.TZ)
        self.patches=[patch.object(s,'now',lambda:self.clock),patch.object(c,'now',lambda:self.clock),patch.object(w,'now',lambda:self.clock),patch.object(w,'STATE',self.state),patch.object(w,'log',lambda *a,**kw:None)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def create(self,thread=A,**kw):
        payload={'time':'07:00','target_type':'thread','thread_id':thread,'prompt':'  第一行\n第二行  '};payload.update(kw)
        return s.create_alarm(self.state,payload,thread_name='研究会话')
    def alarm(self,id):return next(a for a in s.list_alarms(self.state) if a['id']==id)
    def reserve(self,id):return c.reserve(self.state,self.alarm(id),self.clock.replace(second=0))
    def run_row(self,key):
        with s.database(self.state) as db:return dict(db.execute('SELECT * FROM custom_runs WHERE slot=?',(key,)).fetchone())
    def test_parallel_time_targets_and_legacy_default_are_independent(self):
        self.create();self.create(B)
        self.assertEqual(len(c.due_alarms(self.state)),2)
        self.assertEqual(s.enabled_times(self.state),((7,0),(12,5),(17,10)))
        claimed=s.claim_alarm(self.state,self.clock.replace(second=0));self.assertEqual(claimed['target_type'],'default')
        with self.assertRaises(sqlite3.IntegrityError):self.create()
    def test_prompt_is_preserved_and_inference_overrides_forbidden(self):
        id=self.create();self.assertEqual(self.alarm(id)['prompt'],'  第一行\n第二行  ')
        for field in ['model','effort','approvalPolicy','message','run_at']:
            with self.assertRaises(ValueError):s.update_alarm(self.state,id,{field:'override'})
        with self.assertRaises(ValueError):s.create_alarm(self.state,{'time':'08:00','prompt':'changed ok'})
    def test_switch_back_to_default_clears_custom_fields(self):
        id=self.create(time='08:00');s.update_alarm(self.state,id,{'target_type':'default'})
        actual=self.alarm(id);self.assertEqual((actual['target_type'],actual['thread_id'],actual['prompt']),('default','',''))
    def test_duplicate_occurrence_cannot_be_replayed_after_delete_recreate(self):
        id=self.create();slot=self.reserve(id);s.delete_alarm(self.state,id);new=self.create()
        self.assertIsNone(self.reserve(new));self.assertIsNone(c.begin(self.state,slot));self.assertEqual(self.run_row(slot)['status'],'skipped_changed')
    def test_once_consumed_before_execution_and_duplicate_worker_ignored(self):
        self.clock=self.clock.replace(hour=6,minute=59);id=self.create(mode='once');self.clock=self.clock.replace(hour=7,minute=0)
        slot=self.reserve(id);run=c.begin(self.state,slot);self.assertFalse(self.alarm(id)['enabled']);self.assertIsNone(c.begin(self.state,slot))
        c.finish(self.state,run,'completed');self.assertEqual(self.alarm(id)['last_status'],'completed')
        self.clock+=dt.timedelta(days=1);self.assertEqual(c.due_alarms(self.state),[])
    def test_edit_during_run_keeps_new_schedule(self):
        self.clock=self.clock.replace(hour=6,minute=59);id=self.create(mode='once');self.clock=self.clock.replace(hour=7,minute=0)
        run=c.begin(self.state,self.reserve(id));s.update_alarm(self.state,id,{'time':'08:00','enabled':True});c.finish(self.state,run,'completed')
        actual=self.alarm(id);self.assertTrue(actual['enabled']);self.assertIsNone(actual['last_status']);self.assertEqual(actual['time'],'08:00')
    def test_custom_only_alarm_does_not_enter_default_dispatcher(self):
        self.create(time='08:20');self.clock=self.clock.replace(hour=8,minute=20)
        self.assertNotIn((8,20),s.enabled_times(self.state));self.assertIsNone(s.claim_alarm(self.state,self.clock.replace(second=0)))
    def test_missed_once_does_not_catch_up(self):
        self.clock=self.clock.replace(hour=6,minute=59);id=self.create(mode='once');self.clock=self.clock.replace(hour=7,minute=1)
        self.assertEqual(c.due_alarms(self.state),[]);self.assertFalse(self.alarm(id)['enabled'])
    def test_changed_after_reservation_never_sends(self):
        id=self.create();slot=self.reserve(id);s.update_alarm(self.state,id,{'prompt':'new'})
        with patch.object(w,'RPC',side_effect=AssertionError('must not connect')):w.execute(slot)
        self.assertEqual(self.run_row(slot)['status'],'skipped_changed')
    def test_late_worker_never_connects(self):
        id=self.create();slot=self.reserve(id);self.clock+=dt.timedelta(seconds=30)
        with patch.object(w,'RPC',side_effect=AssertionError('must not connect')):w.execute(slot)
        self.assertEqual(self.run_row(slot)['status'],'skipped_missed')
    def test_busy_target_is_skipped_without_resuming_or_starting(self):
        id=self.create();slot=self.reserve(id);rpc=FakeRPC(self,busy=True)
        with patch.object(w,'RPC',lambda:rpc):w.execute(slot)
        self.assertEqual(self.run_row(slot)['status'],'skipped_busy');self.assertFalse(any(m in ('thread/resume','turn/start','thread/start') for m,p in rpc.calls))
    def test_archived_target_restored_and_latest_settings_inherited(self):
        id=self.create();slot=self.reserve(id);rpc=FakeRPC(self,archived=True)
        with patch.object(w,'RPC',lambda:rpc):w.execute(slot)
        self.assertEqual(self.run_row(slot)['status'],'completed')
        self.assertTrue(any(m=='thread/unarchive' for m,p in rpc.calls))
        start=next(p for m,p in rpc.calls if m=='turn/start');self.assertEqual(start['input'][0]['text'],'  第一行\n第二行  ')
        self.assertEqual(set(start),{'threadId','input','clientUserMessageId'})
        resume=next(p for m,p in rpc.calls if m=='thread/resume');self.assertEqual(set(resume),{'threadId','excludeTurns'})
        row=self.run_row(slot);self.assertEqual((row['model'],row['effort']),('gpt-6.1-sol','high'))
    def test_custom_model_and_effort_override_when_specified(self):
        id=self.create(custom_model='gpt-6-luna',custom_effort='max');slot=self.reserve(id);rpc=FakeRPC(self)
        with patch.object(w,'RPC',lambda:rpc):w.execute(slot)
        self.assertEqual(self.run_row(slot)['status'],'completed')
        start=next(p for m,p in rpc.calls if m=='turn/start')
        self.assertEqual((start.get('model'),start.get('effort')),('gpt-6-luna','max'))
        row=self.run_row(slot);self.assertEqual((row['model'],row['effort']),('gpt-6-luna','max'))
    def test_missing_model_settings_fails_without_fallback(self):
        id=self.create();slot=self.reserve(id);rpc=FakeRPC(self,effort=None)
        with patch.object(w,'RPC',lambda:rpc):w.execute(slot)
        self.assertEqual(self.run_row(slot)['status'],'failed_or_uncertain');self.assertFalse(any(m=='turn/start' for m,p in rpc.calls))
    def test_long_turn_has_no_old_three_minute_timeout(self):
        id=self.create();slot=self.reserve(id);rpc=FakeRPC(self,long=True)
        with patch.object(w,'RPC',lambda:rpc):w.execute(slot)
        self.assertEqual(self.run_row(slot)['status'],'completed');self.assertGreaterEqual((self.clock-dt.datetime(2026,10,16,7,0,1,tzinfo=s.TZ)).total_seconds(),600)
        self.assertFalse(any(m=='turn/interrupt' for m,p in rpc.calls))
    def test_managed_daily_session_rejected_even_after_rename(self):
        with s.database(self.state) as db:
            db.execute('CREATE TABLE daily_chats(day TEXT PRIMARY KEY,thread_id TEXT,created_at TEXT)');db.execute('INSERT INTO daily_chats VALUES(?,?,?)',('2026-10-16',A,''))
        self.assertTrue(catalog.is_managed({'id':A,'name':'renamed'},catalog.managed_ids(self.state)))
        self.assertTrue(catalog.is_managed({'id':B,'name':'每日定时触发 2026-10-16'},set()))
    def test_worker_unit_has_no_runtime_limit_or_restart(self):
        with patch.object(d.subprocess,'run') as run:d.launch('slot')
        command=run.call_args.args[0];self.assertIn('--property=RuntimeMaxSec=infinity',command);self.assertIn('--property=Restart=no',command)

class FakeRPC:
    def __init__(self,test,busy=False,archived=False,effort='high',long=False):
        self.test=test;self.busy=busy;self.archived=archived;self.effort=effort;self.long=long;self.calls=[];self.events=[];self.sent=False
    def thread(self):return {'id':A,'name':'研究会话','model':'gpt-6.1-sol','reasoningEffort':self.effort,'status':{'type':'active' if self.busy else 'idle'},'path':'/x/archived_sessions/a' if self.archived else '/x/sessions/a'}
    def call(self,method,params):
        self.calls.append((method,params))
        if method=='thread/read':return {'thread':self.thread()}
        if method=='thread/unarchive':self.archived=False;return {'thread':self.thread()}
        if method=='thread/resume':return {'thread':self.thread(),'model':'gpt-6.1-sol','reasoningEffort':self.effort}
        if method=='turn/start':
            self.sent=True;self.events.append({'method':'turn/started','params':{'threadId':A,'turn':{'id':'turn'}}});return {'turn':{'id':'turn','status':'inProgress'}}
        if method=='thread/turns/list':return {'data':[{'id':'turn','status':'completed'}],'nextCursor':None}
        if method=='thread/items/list':return {'data':[{'item':{'type':'userMessage','content':[{'type':'text','text':'  第一行\n第二行  '}]}}],'nextCursor':None}
        raise AssertionError(method)
    def recv(self,timeout):
        if self.long:self.test.clock+=dt.timedelta(minutes=10)
        return {'method':'turn/completed','params':{'threadId':A,'turn':{'id':'turn','status':'completed'}}}
    def close(self):pass

if __name__=='__main__':unittest.main()
