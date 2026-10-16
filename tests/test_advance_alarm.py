import os
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import schedule_store as store


class AdvanceAlarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name);self.current=dt.datetime(2026,10,17,10,0,tzinfo=store.TZ)
        p=patch.object(store,'now',side_effect=lambda:self.current);p.start();self.addCleanup(p.stop)
        with store.database(self.state) as db:db.execute('DELETE FROM alarms')

    def alarm(self,time='07:00',mode='daily',**more):return store.create_alarm(self.state,{'time':time,'mode':mode,**more})
    def get(self,id):
        with store.database(self.state) as db:return dict(db.execute('SELECT * FROM alarms WHERE id=?',(id,)).fetchone())

    def test_daily_preserves_account_and_repeat_original_unchanged(self):
        source=self.alarm(account_id='a'*20,enabled=False);before=self.get(source)
        target=self.get(store.advance_alarm(self.state,source))
        self.assertEqual(target['time'],'12:01');self.assertEqual(target['account_id'],'a'*20)
        self.assertEqual(target['mode'],'daily');self.assertTrue(target['enabled']);self.assertIsNone(target['run_at'])
        self.assertEqual(self.get(source),before)

    def test_daily_wraps_midnight(self):
        target=self.get(store.advance_alarm(self.state,self.alarm('23:59')))
        self.assertEqual(target['time'],'05:00')

    def test_once_keeps_tomorrow_date_even_when_shifted_time_is_today_future(self):
        source=self.alarm('09:00','once');target=self.get(store.advance_alarm(self.state,source))
        self.assertEqual(target['run_at'],'2026-10-18T14:01:00+08:00')
        self.assertEqual(target['mode'],'once')

    def test_once_crosses_midnight_from_original_date(self):
        source=self.alarm('23:59','once');target=self.get(store.advance_alarm(self.state,source))
        self.assertEqual(target['run_at'],'2026-10-18T05:00:00+08:00')

    def test_finished_once_can_chain_only_when_new_slot_is_future(self):
        self.current=self.current.replace(hour=6);source=self.alarm('07:00','once')
        self.current=self.current.replace(hour=9)
        with store.database(self.state) as db:db.execute('UPDATE alarms SET enabled=0,consumed_at=? WHERE id=?',(self.current.isoformat(),source))
        self.assertEqual(self.get(store.advance_alarm(self.state,source))['run_at'],'2026-10-17T12:01:00+08:00')
        self.current=self.current.replace(hour=15)
        with self.assertRaises(ValueError):store.advance_alarm(self.state,source)

    def test_double_click_does_not_create_duplicates(self):
        source=self.alarm();store.advance_alarm(self.state,source)
        with self.assertRaises(sqlite3.IntegrityError):store.advance_alarm(self.state,source)
        self.assertEqual(len(store.list_alarms(self.state)),2)

    def test_custom_session_and_deleted_source_rejected(self):
        source=self.alarm(target_type='thread',thread_id='00000000-0000-0000-0000-000000000001',prompt='work')
        with self.assertRaises(ValueError):store.advance_alarm(self.state,source)
        store.delete_alarm(self.state,source)
        with self.assertRaises(LookupError):store.advance_alarm(self.state,source)

    def test_follow_main_and_explicit_accounts_are_preserved_and_conflicts_rejected(self):
        source=self.alarm(account_id='a'*20)
        self.alarm('12:01')
        with self.assertRaises(ValueError):store.advance_alarm(self.state,source)
