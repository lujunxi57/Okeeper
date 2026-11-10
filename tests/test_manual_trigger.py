import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

import account_store as store
import account_worker as worker
from schedule_store import database, list_alarms, snapshot, TZ
from session_catalog import managed_ids

ACCOUNT='a'*20
RECORD={'id':ACCOUNT,'label':'alice@example.com','key':('workspace','user','alice@example.com')}


class ManualTriggerTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(temporary.cleanup)
        self.state=Path(temporary.name)
        self.clock=dt.datetime(2026,10,18,5,26,30,tzinfo=TZ)
        for name,value in [('schedule_store.now',lambda:self.clock),('account_store.now',lambda:self.clock),
                           ('account_worker.now',lambda:self.clock),('account_worker.STATE',self.state),('account_worker.log',Mock())]:
            p=patch(name,value);p.start();self.addCleanup(p.stop)
        self.request_id=str(uuid.uuid4())

    def reserve(self,record=RECORD,request_id=None):
        return store.reserve_manual(self.state,record,request_id or self.request_id)

    def row(self,slot):
        with database(self.state) as db:
            return dict(db.execute('SELECT * FROM account_runs WHERE slot=?',(slot,)).fetchone())

    def test_retries_reuse_receipt_even_after_completion(self):
        slot,created=self.reserve();self.assertTrue(created)
        self.assertEqual(self.reserve(),(slot,False))
        run=store.begin(self.state,slot);store.finish(self.state,run,'completed',tokens=12)
        self.assertEqual(self.reserve(),(slot,False))
        self.assertIsNone(store.begin(self.state,slot))
        self.assertEqual(snapshot(self.state)['stats']['trigger_count'],1)

    def test_concurrent_different_click_is_rejected_and_accounts_are_independent(self):
        self.reserve()
        with self.assertRaises(ValueError):self.reserve(request_id=str(uuid.uuid4()))
        other={**RECORD,'id':'b'*20}
        self.assertTrue(self.reserve(record=other)[1])

    def test_scheduled_account_job_also_prevents_duplicate_manual_send(self):
        with database(self.state) as db:
            db.execute('INSERT INTO account_runs(slot,alarm_id,revision,account_id,account_name,scheduled,status,at) VALUES(?,?,?,?,?,?,?,?)',
                       ('scheduled','alarm',0,ACCOUNT,RECORD['label'],self.clock.isoformat(),'accepted',self.clock.isoformat()))
        with self.assertRaises(ValueError):self.reserve()

    def test_manual_send_preserves_every_alarm_and_absolute_request_time(self):
        before=list_alarms(self.state)
        slot,_=self.reserve()
        self.assertEqual(list_alarms(self.state),before)
        self.assertEqual(self.row(slot)['scheduled'],self.clock.isoformat())

    def test_invalid_request_ids_are_rejected(self):
        for value in [None,{},[],12,'','../../command',self.request_id.upper()]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                store.reserve_manual(self.state,RECORD,value)

    def test_worker_uses_verified_account_without_old_daily_pin_and_sends_once(self):
        slot,_=self.reserve();rpc=Mock()
        with patch.object(worker,'find_account',return_value=RECORD), \
             patch.object(worker,'open_account',return_value=(rpc,'isolated')) as opened, \
             patch.object(worker,'daily_chat',return_value=('manual-chat',True)) as daily, \
             patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'cleanup'),patch.object(worker,'run_turn',return_value={'turn_id':'turn','tokens':12}) as send:
            worker.process(slot);worker.process(slot)
        opened.assert_called_once_with(self.state,RECORD);daily.assert_called_once_with(RECORD,rpc,'isolated','2026-10-18')
        send.assert_called_once()
        self.assertEqual(self.row(slot)['status'],'completed')
        self.assertEqual(self.row(slot)['thread_id'],'manual-chat')
        with database(self.state) as db:self.assertEqual(db.execute('SELECT count(*) FROM account_daily_chats').fetchone()[0],0)

    def test_wrong_account_never_sends_and_uncertainty_never_retries(self):
        slot,_=self.reserve();rpc=Mock()
        with patch.object(worker,'find_account',return_value=RECORD), \
             patch.object(worker,'open_account',return_value=(rpc,'isolated')),patch.object(worker,'matches',return_value=False), \
             patch.object(worker,'create_chat',return_value='manual-chat'),patch.object(worker,'cleanup'),patch.object(worker,'run_turn') as send:
            worker.process(slot);worker.process(slot)
        send.assert_not_called();self.assertEqual(self.row(slot)['status'],'skipped_account_changed')

    def test_timeout_is_reported_without_replaying_original_request(self):
        slot,_=self.reserve();self.clock+=dt.timedelta(seconds=316)
        result=store.manual_runs(self.state)[ACCOUNT]
        self.assertEqual(result['status'],'failed_or_uncertain')
        self.assertEqual(self.reserve(),(slot,False));self.assertIsNone(store.begin(self.state,slot))
        self.assertTrue(self.reserve(request_id=str(uuid.uuid4()))[1])

    def test_manual_main_chat_is_excluded_from_custom_targets(self):
        slot,_=self.reserve();store.update(self.state,slot,'completed',route='main',thread_id='manual-chat')
        self.assertIn('manual-chat',managed_ids(self.state))

    def test_two_manual_sends_reuse_daily_chat_instead_of_creating_per_click(self):
        first,_=self.reserve();rpc=Mock()
        rpc.call.return_value={'thread':{'status':{'type':'idle'},'path':'/sessions/test'}}
        with patch.object(worker,'find_account',return_value=RECORD),patch.object(worker,'matches',return_value=True), \
             patch.object(worker,'open_account',return_value=(rpc,'main')),patch.object(worker,'cleanup'), \
             patch.object(worker,'create_chat',return_value='shared-manual-daily') as create, \
             patch.object(worker,'run_turn',return_value={'turn_id':'turn'}):
            worker.process(first)
            second,_=self.reserve(request_id=str(uuid.uuid4()));worker.process(second)
        create.assert_called_once()
        self.assertEqual(self.row(first)['thread_id'],self.row(second)['thread_id'])
