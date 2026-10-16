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

    def test_account_binding_cannot_be_added_to_custom_session(self):
        with self.assertRaises(ValueError):create_alarm(self.state,{'time':'07:01','target_type':'thread','thread_id':'00000000-0000-4000-8000-000000000001','prompt':'task','account_id':ACCOUNT})

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
            {'thread':{'turns':[{'error':{'codexErrorInfo':'usageLimitExceeded','message':'private upstream details'}}]}})
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

    def test_account_change_before_send_skips_without_migrating_or_switching(self):
        _,slot=self.reserve();main=Mock();isolated=Mock()
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'open_account',return_value=(main,'main')), \
             patch.object(worker,'daily_chat',return_value=('main-thread',True)), \
             patch.object(worker,'matches',return_value=False),patch.object(worker,'IsolatedRPC',return_value=isolated), \
             patch.object(worker,'run_turn',return_value={'turn_id':'completed-turn'}) as send,patch.object(worker,'cleanup'):
            worker.process(slot)
        send.assert_not_called()
        main.close.assert_called_once()
        with database(self.state) as db:
            row=db.execute('SELECT route,status FROM account_runs').fetchone()
            self.assertEqual(tuple(row),('main','skipped_account_changed'))

    def test_first_isolated_home_stays_pinned_when_account_becomes_main(self):
        with database(self.state) as db:db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','isolated','daily-thread',''))
        isolated=Mock()
        with patch.object(worker,'IsolatedRPC',return_value=isolated),patch.object(worker,'open_account') as choose:
            rpc,route=worker.open_for_day(RECORD,'2026-10-16')
        choose.assert_not_called();self.assertIs(rpc,isolated);self.assertEqual(route,'isolated')

    def test_pinned_main_with_changed_account_skips_without_starting_other_home(self):
        with database(self.state) as db:db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','main','daily-thread',''))
        main=Mock()
        with patch.object(worker,'RPC',return_value=main),patch.object(worker,'matches',return_value=False),patch.object(worker,'IsolatedRPC') as other:
            with self.assertRaises(worker.PinnedAccountChanged):worker.open_for_day(RECORD,'2026-10-16')
        main.close.assert_called_once();other.assert_not_called()

    def test_next_day_selects_runtime_again(self):
        with database(self.state) as db:db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-15','isolated','daily-thread',''))
        with patch.object(worker,'open_account',return_value=(Mock(),'main')) as choose:
            _,route=worker.open_for_day(RECORD,'2026-10-16')
        self.assertEqual(route,'main');choose.assert_called_once()

    def test_same_day_cannot_create_another_chat_in_a_different_backend(self):
        with database(self.state) as db:db.execute('INSERT INTO account_daily_chats VALUES(?,?,?,?,?)',(ACCOUNT,'2026-10-16','isolated','daily-thread',''))
        with self.assertRaises(worker.PinnedAccountChanged):worker.daily_chat(RECORD,Mock(),'main','2026-10-16')

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
        self.assertEqual([c.args[0] for c in rpc.call.call_args_list],['thread/read','thread/archive','thread/delete'])
        self.assertEqual([c.args[1].get('threadId') for c in rpc.call.call_args_list],['old']*3)
        with database(self.state) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM account_daily_chats').fetchone()[0],3)

    def test_original_rows_survive_upgrade_with_follow_main_binding(self):
        alarm=create_alarm(self.state,{'time':'08:00','enabled':False})
        item=next(a for a in list_alarms(self.state) if a['id']==alarm)
        self.assertEqual(item['account_id'],'');self.assertEqual(item['time'],'08:00');self.assertFalse(item['enabled'])


if __name__=='__main__':unittest.main()
