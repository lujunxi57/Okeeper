import os
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch

import account_rpc as routing
import account_store as store
import account_worker as worker
import trigger
from test_turn_observation import Clock, EventRPC
from turn_result import FAILURE_MESSAGES
from schedule_store import create_alarm,database,delete_alarm,enabled_times,list_alarms,update_alarm


ACCOUNT='a'*20
RECORD={'id':ACCOUNT,'key':('workspace-a','user-a','alice@example.com'),'label':'alice@example.com'}


class AccountRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)
        self.clock=dt.datetime(2026,10,16,7,0,1,tzinfo=dt.timezone(dt.timedelta(hours=8)))
        for name,target in [('schedule_store.now',lambda:self.clock),('account_store.now',lambda:self.clock),
                            ('account_worker.now',lambda:self.clock),('account_worker.STATE',self.state),
                            ('account_worker.log',Mock())]:
            p=patch(name,target);p.start();self.addCleanup(p.stop)
        # Clear seeded legacy alarms so account schedules are explicit fixtures.
        for alarm in list_alarms(self.state):delete_alarm(self.state,alarm['id'])

    def reserve(self,mode='once',account=ACCOUNT):
        alarm_id=create_alarm(self.state,{'time':'07:01','mode':mode,'account_id':account},account_name='alice@example.com')
        self.clock=self.clock.replace(minute=1,second=1)
        alarm=next(a for a in list_alarms(self.state) if a['id']==alarm_id)
        slot=store.reserve(self.state,alarm,self.clock.replace(second=0))
        return alarm,slot

    def test_main_account_uses_current_server_without_login_or_isolated_process(self):
        main=Mock()
        with patch.object(routing,'RPC',return_value=main),patch.object(routing,'matches',return_value=True),patch.object(routing,'IsolatedRPC') as child:
            rpc,route=routing.open_account(self.state,RECORD)
        self.assertIs(rpc,main);self.assertEqual(route,'main');child.assert_not_called();main.close.assert_not_called()

    def test_other_account_gets_its_own_home_and_leaves_main_login_untouched(self):
        main=Mock();isolated=Mock()
        with patch.object(routing,'RPC',return_value=main),patch.object(routing,'matches',return_value=False),patch.object(routing,'IsolatedRPC',return_value=isolated) as child:
            rpc,route=routing.open_account(self.state,RECORD)
        self.assertEqual(route,'isolated');self.assertIs(rpc,isolated)
        child.assert_called_once_with(self.state/'account-homes'/ACCOUNT,RECORD)
        main.close.assert_called_once();main.call.assert_not_called()

    def test_workspace_alone_never_matches_a_different_user(self):
        rpc=Mock();rpc.call.return_value={'account':{'type':'chatgpt','email':'someone-else@example.com'},'workspaceRouting':{'chatgptAccountId':'workspace-a'}}
        self.assertFalse(routing.matches(rpc,RECORD))
        rpc.call.assert_called_once_with('account/read',{'refreshToken':False})

    def test_two_accounts_at_same_time_and_original_paths_remain_independent(self):
        a,_=self.reserve(mode='daily')
        create_alarm(self.state,{'time':'07:01','account_id':'b'*20})
        create_alarm(self.state,{'time':'07:01','target_type':'thread','thread_id':'00000000-0000-4000-8000-000000000001','prompt':'task'})
        self.assertEqual(len(store.due_alarms(self.state,self.clock)),2)
        self.assertEqual(enabled_times(self.state,self.clock),())

    def test_follow_main_and_explicit_account_at_same_time_cannot_double_send(self):
        create_alarm(self.state,{'time':'07:01'})
        with self.assertRaises(ValueError):create_alarm(self.state,{'time':'07:01','account_id':ACCOUNT})

    def test_once_is_closed_before_dispatch_and_target_occurrence_is_durable(self):
        alarm,slot=self.reserve()
        self.assertFalse(next(a for a in list_alarms(self.state) if a['id']==alarm['id'])['enabled'])
        self.assertIsNone(store.reserve(self.state,alarm,self.clock.replace(second=0)))
        run=store.begin(self.state,slot);self.assertIsNotNone(run);self.assertIsNone(store.begin(self.state,slot))

    def test_custom_session_can_bind_account_and_keep_it_when_edited(self):
        id=create_alarm(self.state,{'time':'07:01','target_type':'thread','thread_id':'00000000-0000-4000-8000-000000000001','prompt':'task','account_id':ACCOUNT},account_name='alice@example.com')
        update_alarm(self.state,id,{'prompt':'edited'})
        actual=next(a for a in list_alarms(self.state) if a['id']==id)
        self.assertEqual((actual['account_id'],actual['account_name']),(ACCOUNT,'alice@example.com'))

    def test_late_dispatch_never_opens_main_or_isolated_server(self):
        _,slot=self.reserve();self.clock+=dt.timedelta(seconds=20)
        with patch.object(worker,'open_account') as open_rpc:worker.process(slot)
        open_rpc.assert_not_called()
        with database(self.state) as db:self.assertEqual(db.execute('SELECT status FROM account_runs').fetchone()[0],'skipped_missed')

    def test_changed_alarm_cancels_queued_job(self):
        alarm,slot=self.reserve();update_alarm(self.state,alarm['id'],{'time':'08:02'})
        with patch.object(worker,'open_account') as open_rpc:worker.process(slot)
        open_rpc.assert_not_called()

    def test_missing_account_never_falls_back_to_current_login(self):
        _,slot=self.reserve()
        with patch.object(worker,'find_account',return_value=None),patch.object(worker,'open_account') as open_rpc:worker.process(slot)
        open_rpc.assert_not_called()

    def test_uncertain_send_is_never_retried_in_another_home(self):
        _,slot=self.reserve();rpc=Mock()
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'open_account',return_value=(rpc,'main')), \
             patch.object(worker,'daily_chat',return_value=('thread-a',True)),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'run_turn',side_effect=TimeoutError()) as send,patch.object(worker,'IsolatedRPC') as child:
            worker.process(slot);worker.process(slot)
        send.assert_called_once();child.assert_not_called()
        with database(self.state) as db:self.assertEqual(db.execute('SELECT status FROM account_runs').fetchone()[0],'failed_or_uncertain')

    def test_failed_turn_keeps_accepted_turn_id_and_reports_quota_reason(self):
        _,slot=self.reserve();rpc=Mock()
        rpc.call.side_effect=lambda method,params: ({'turn':{'id':'accepted-turn'}} if method=='turn/start' else
            {'thread':{'id':'thread-a','turns':[{'id':'accepted-turn','status':'failed',
                'error':{'codexErrorInfo':'usageLimitExceeded','message':'private upstream details'}}]}})
        def fail_turn(observed,thread,key,scheduled):
            observed.call('turn/start',{'threadId':thread,'input':[{'type':'text','text':'ok'}]})
            raise RuntimeError('turn ended: failed')
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'open_account',return_value=(rpc,'main')), \
             patch.object(worker,'daily_chat',return_value=('thread-a',True)),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'run_turn',side_effect=fail_turn),patch.object(worker,'cleanup'):
            worker.process(slot)
        with database(self.state) as db:
            row=db.execute('SELECT turn_id,status,error FROM account_runs').fetchone()
            self.assertEqual(row['turn_id'],'accepted-turn');self.assertEqual(row['status'],'failed_or_uncertain')
            self.assertIn('额度已用尽',row['error']);self.assertNotIn('private',row['error'])

    def test_delayed_acceptance_completes_before_closing_on_both_routes_and_never_replays(self):
        for route in ('main','isolated'):
            with self.subTest(route=route):
                account=ACCOUNT if route=='main' else 'b'*20
                record={**RECORD,'id':account}
                _,slot=self.reserve(account=account)
                scheduled=self.clock.replace(second=0)
                clock=Clock();clock.scheduled=scheduled
                rpc=EventRPC(clock)
                def closing():
                    with database(self.state) as db:
                        row=db.execute('SELECT status,turn_id FROM account_runs WHERE slot=?',(slot,)).fetchone()
                    self.assertEqual(tuple(row),('completed','turn'))
                    self.assertGreaterEqual(clock.seconds,20)
                rpc.on_close=closing
                with patch.object(worker,'now',clock.now),patch('schedule_store.now',clock.now), \
                     patch.object(trigger,'now',clock.now),patch.object(trigger.time,'monotonic',lambda:clock.seconds), \
                     patch.object(trigger,'log'),patch.object(worker,'find_account',return_value=record), \
                     patch.object(worker,'open_account',return_value=(rpc,route)) as opened, \
                     patch.object(worker,'daily_chat',return_value=('thread',True)), \
                     patch.object(worker,'matches',return_value=True),patch.object(worker,'cleanup'):
                    worker.process(slot);worker.process(slot)
                opened.assert_called_once()
                self.assertEqual(sum(m=='turn/start' for m,_ in rpc.calls),1)
                self.assertTrue(rpc.closed)

    def test_preparation_finishing_after_deadline_never_submits(self):
        _,slot=self.reserve();rpc=Mock()
        def slow_prepare(*args):
            self.clock=self.clock.replace(second=16)
            return 'thread',True
        with patch.object(worker,'find_account',return_value=RECORD), \
             patch.object(worker,'open_account',return_value=(rpc,'isolated')), \
             patch.object(worker,'daily_chat',side_effect=slow_prepare),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'cleanup'),patch.object(worker,'run_turn') as send:
            worker.process(slot)
        send.assert_not_called()
        with database(self.state) as db:
            row=db.execute('SELECT status,turn_id FROM account_runs WHERE slot=?',(slot,)).fetchone()
        self.assertEqual(tuple(row),('skipped_missed',None))

    def test_failure_reason_never_uses_previous_turn_quota_error(self):
        rpc=Mock();rpc.call.return_value={'thread':{'id':'thread','turns':[
            {'id':'previous','status':'failed','error':{'codexErrorInfo':'usageLimitExceeded'}},
            {'id':'accepted','status':'interrupted'}]}}
        observed=worker.ObservedTurn(rpc,'slot');observed.turn_id='accepted';observed.submit_started=True
        self.assertEqual(worker.failure_reason(rpc,'thread',observed,RuntimeError()),
                         FAILURE_MESSAGES['turn_interrupted'])

    def test_account_change_during_preparation_uses_isolated_before_sending(self):
        _,slot=self.reserve();main=Mock();isolated=Mock()
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'open_account',return_value=(main,'main')), \
             patch.object(worker,'daily_chat',side_effect=[('main-thread',True),('isolated-thread',True)]), \
             patch.object(worker,'matches',side_effect=[True,False,True,True]),patch.object(worker,'IsolatedRPC',return_value=isolated), \
             patch.object(worker,'run_turn',return_value={'turn_id':'completed-turn'}) as send,patch.object(worker,'cleanup'):
            worker.process(slot)
        send.assert_called_once()
        self.assertIs(send.call_args.args[0].rpc,isolated)
        main.close.assert_called_once()
        with database(self.state) as db:
            row=db.execute('SELECT route,status FROM account_runs').fetchone()
            self.assertEqual(tuple(row),('isolated','completed'))

    def test_first_isolated_home_does_not_pin_later_trigger(self):
        with database(self.state) as db:db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','isolated','daily-thread',''))
        _,slot=self.reserve();main=Mock()
        with patch.object(worker,'open_account',return_value=(main,'main')) as choose, \
             patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'daily_chat',return_value=('main-thread',True)),patch.object(worker,'cleanup'), \
             patch.object(worker,'run_turn',return_value={'turn_id':'turn'}):
            worker.process(slot)
        choose.assert_called_once_with(self.state,RECORD)
        with database(self.state) as db:self.assertEqual(db.execute('SELECT route FROM account_runs').fetchone()[0],'main')

    def test_existing_main_chat_does_not_prevent_isolated_route(self):
        with database(self.state) as db:db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','main','daily-thread',''))
        _,slot=self.reserve();isolated=Mock()
        with patch.object(worker,'open_account',return_value=(isolated,'isolated')) as choose, \
             patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'daily_chat',return_value=('isolated-thread',True)),patch.object(worker,'cleanup'), \
             patch.object(worker,'run_turn',return_value={'turn_id':'turn'}):worker.process(slot)
        choose.assert_called_once()
        with database(self.state) as db:self.assertEqual(db.execute('SELECT route FROM account_runs').fetchone()[0],'isolated')

    def test_same_day_reuses_one_chat_per_account_and_route(self):
        with database(self.state) as db:db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','isolated','daily-thread',''))
        rpc=Mock();rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/test'}}
        with patch.object(worker,'create_chat',return_value='main-thread') as create:
            self.assertEqual(worker.daily_chat(RECORD,rpc,'main','2026-10-16'),('main-thread',True))
            self.assertEqual(worker.daily_chat(RECORD,rpc,'isolated','2026-10-16'),('daily-thread',True))
            self.assertEqual(worker.daily_chat(RECORD,rpc,'main','2026-10-16'),('main-thread',True))
        create.assert_called_once()

    def test_one_daily_chat_per_account_and_runtime_is_reused(self):
        rpc=Mock();rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/t.jsonl'}}
        with patch.object(worker,'create_chat',return_value='thread-a') as create:
            first=worker.daily_chat(RECORD,rpc,'main','2026-10-16')
            second=worker.daily_chat(RECORD,rpc,'main','2026-10-16')
        self.assertEqual(first,('thread-a',True));self.assertEqual(first,second);create.assert_called_once()

    def test_cleanup_archives_then_deletes_only_registered_past_chats(self):
        with database(self.state) as db:
            for account,day,route,thread in [(ACCOUNT,'2026-10-15','main','old'),(ACCOUNT,'2026-10-16','main','today'),
                                             ('b'*20,'2026-10-15','main','other'),(ACCOUNT,'2026-10-14','isolated','other-home')]:
                db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(account,day,route,thread,''))
        rpc=Mock();rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/old'}}
        worker.cleanup(RECORD,rpc,'main','2026-10-16','today')
        self.assertEqual([c.args[0] for c in rpc.call.call_args_list],['thread/read','thread/archive','thread/delete']*2)
        self.assertEqual([c.args[1].get('threadId') for c in rpc.call.call_args_list],['old']*3+['other']*3)
        with database(self.state) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM account_daily_chats').fetchone()[0],2)

    def test_both_routes_create_new_chat_on_next_day(self):
        rpc=Mock();rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/test'}}
        with patch.object(worker,'create_chat',side_effect=['main-today','isolated-today','main-tomorrow','isolated-tomorrow']) as create:
            for day in ('2026-10-16','2026-10-17'):
                for route in ('main','isolated'):
                    first=worker.daily_chat(RECORD,rpc,route,day)
                    self.assertEqual(worker.daily_chat(RECORD,rpc,route,day),first)
        self.assertEqual(create.call_count,4)

    def test_two_accounts_in_main_have_distinct_daily_chats(self):
        other={**RECORD,'id':'b'*20,'label':'bob@example.com'}
        rpc=Mock();rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/test'}}
        with patch.object(worker,'create_chat',side_effect=['alice-chat','bob-chat']) as create:
            self.assertEqual(worker.daily_chat(RECORD,rpc,'main','2026-10-16')[0],'alice-chat')
            self.assertEqual(worker.daily_chat(other,rpc,'main','2026-10-16')[0],'bob-chat')
            self.assertEqual(worker.daily_chat(RECORD,rpc,'main','2026-10-16')[0],'alice-chat')
        self.assertEqual(create.call_count,2)

    def test_main_isolated_main_same_day_reuses_own_chats_and_never_replays_slot(self):
        _,first=self.reserve(mode='daily');main=Mock();isolated=Mock()
        for rpc in (main,isolated):rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/test'}}
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'open_account',side_effect=[(main,'main'),(isolated,'isolated'),(main,'main')]), \
             patch.object(worker,'create_chat',side_effect=['main-chat','isolated-chat']) as create, \
             patch.object(worker,'cleanup'),patch.object(worker,'run_turn',return_value={'turn_id':'turn'}) as send:
            worker.process(first)
            for minute in (2,3):
                self.clock=self.clock.replace(minute=minute)
                alarm=list_alarms(self.state)[0]
                slot=store.reserve(self.state,alarm,self.clock.replace(second=0))
                worker.process(slot);worker.process(slot)
        self.assertEqual([call.args[1] for call in send.call_args_list],['main-chat','isolated-chat','main-chat'])
        self.assertEqual(create.call_count,2)
        with database(self.state) as db:
            self.assertEqual([r['route'] for r in db.execute('SELECT route FROM account_runs ORDER BY scheduled')],['main','isolated','main'])

    def test_already_accepted_turn_is_not_replayed_after_account_moves(self):
        _,slot=self.reserve();rpc=Mock();rpc.call.return_value={'turn':{'id':'accepted'}}
        def disconnected(observed,thread,key,scheduled):
            observed.call('turn/start',{})
            raise ConnectionError()
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'open_account',return_value=(rpc,'main')) as choose, \
             patch.object(worker,'daily_chat',return_value=('chat',True)),patch.object(worker,'cleanup'), \
             patch.object(worker,'run_turn',side_effect=disconnected) as send:
            worker.process(slot);worker.process(slot)
        choose.assert_called_once();send.assert_called_once()
        with database(self.state) as db:
            row=db.execute('SELECT turn_id,status FROM account_runs').fetchone()
            self.assertEqual(tuple(row),('accepted','failed_or_uncertain'))

    def test_trigger_cleans_both_homes_only_after_sending(self):
        _,slot=self.reserve();main=Mock();isolated=Mock();events=[]
        with database(self.state) as db:
            for route in ('main','isolated'):
                db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-15',route,'old-'+route,''))
        def cleanup(record,rpc,route,day,current_thread):events.append('cleanup-'+route)
        def send(*args):events.append('send');return {'turn_id':'turn'}
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'open_account',return_value=(main,'main')),patch.object(worker,'IsolatedRPC',return_value=isolated), \
             patch.object(worker,'daily_chat',return_value=('main-chat',True)),patch.object(worker,'cleanup',side_effect=cleanup), \
             patch.object(worker,'run_turn',side_effect=send):worker.process(slot)
        self.assertEqual(events,['send','cleanup-main','cleanup-isolated'])
        main.close.assert_called_once();isolated.close.assert_called_once()

    def test_legacy_unique_index_is_removed_without_losing_registered_chats(self):
        with database(self.state) as db:
            db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','main','old-main',''))
            db.execute('CREATE UNIQUE INDEX account_daily_one_chat ON account_daily_chats(account_id,day)')
        with database(self.state) as db:
            db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','isolated','new-isolated',''))
            self.assertEqual({r['thread_id'] for r in db.execute('SELECT * FROM account_daily_chats')},{'old-main','new-isolated'})

    def test_cleanup_defers_busy_and_failed_deletes_keeps_today_and_other_home(self):
        with database(self.state) as db:
            for day,route,thread in [('2026-10-15','main','busy'),('2026-10-15','main','failed')]:
                # Separate accounts permit two chats on the same day/route.
                account=ACCOUNT if thread=='busy' else 'b'*20
                db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(account,day,route,thread,''))
        rpc=Mock()
        def call(method,params):
            if method=='thread/delete':raise ConnectionError()
            return {'thread':{'status':{'type':'active' if params['threadId']=='busy' else 'idle'},'path':'/sessions/test'}}
        rpc.call.side_effect=call
        worker.cleanup(RECORD,rpc,'main','2026-10-16',None)
        with database(self.state) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM account_daily_chats').fetchone()[0],2)
        self.assertFalse(any(c.args[0]=='thread/delete' and c.args[1]['threadId']=='busy' for c in rpc.call.call_args_list))

    def test_cleanup_old_manual_chats_keeps_receipts_and_does_not_delete_again(self):
        with database(self.state) as db:
            db.execute('INSERT INTO account_runs(slot,alarm_id,revision,account_id,account_name,scheduled,status,at,route,thread_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('account-manual-v1:old','',0,ACCOUNT,'alice','2026-10-15T20:00:00+08:00','completed','','main','old-manual'))
        rpc=Mock();rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/test'}}
        worker.cleanup(RECORD,rpc,'main','2026-10-16',None)
        worker.cleanup(RECORD,rpc,'main','2026-10-16',None)
        self.assertEqual([c.args[0] for c in rpc.call.call_args_list],['thread/read','thread/archive','thread/delete'])
        with database(self.state) as db:self.assertEqual(db.execute('SELECT thread_id FROM account_runs').fetchone()[0],'old-manual')

    def test_original_rows_survive_upgrade_with_follow_main_binding(self):
        alarm=create_alarm(self.state,{'time':'08:00','enabled':False})
        item=next(a for a in list_alarms(self.state) if a['id']==alarm)
        self.assertEqual(item['account_id'],'');self.assertEqual(item['time'],'08:00');self.assertFalse(item['enabled'])


if __name__=='__main__':unittest.main()
