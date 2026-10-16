import datetime as dt
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import schedule_store as s

class OnceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.clock = dt.datetime(2026,10,13,20,25,0,tzinfo=s.TZ)
        self.p = patch.object(s, 'now', lambda: self.clock); self.p.start()
    def tearDown(self):
        self.p.stop(); self.temp.cleanup()
    def alarm(self, alarm_id):
        return next(a for a in s.list_alarms(self.state) if a['id']==alarm_id)
    def test_original_alarm_can_edit_mode_and_time_with_same_identity(self):
        old = s.list_alarms(self.state)[0]
        s.update_alarm(self.state, old['id'], {'mode':'once'})
        one = self.alarm(old['id'])
        self.assertEqual(one['mode'], 'once')
        self.assertEqual(one['run_at'], '2026-10-14T07:00:00+08:00')
        s.update_alarm(self.state, old['id'], {'mode':'daily','time':'09:42'})
        edited = self.alarm(old['id'])
        self.assertEqual(edited['time'], '09:42')
        self.assertEqual(edited['mode'], 'daily')
        self.assertIsNone(edited['run_at'])
        self.assertEqual(edited['created_at'], old['created_at'])
    def test_once_crosses_midnight_without_repeating_next_day(self):
        self.clock = self.clock.replace(hour=23,minute=58)
        alarm = s.create_alarm(self.state, {'time':'00:01','mode':'once'})
        due = dt.datetime(2026,10,14,0,1,tzinfo=s.TZ)
        self.assertEqual(self.alarm(alarm)['run_at'],due.isoformat())
        self.assertNotIn((0,1),s.enabled_times(self.state,self.clock))
        self.clock=due+dt.timedelta(seconds=1)
        claimed=s.claim_alarm(self.state,due)
        self.assertIsNotNone(claimed)
        self.assertFalse(self.alarm(alarm)['enabled'])
        s.finish_alarm(self.state,claimed,'completed')
        self.assertEqual(self.alarm(alarm)['last_status'],'completed')
        self.assertIsNone(s.claim_alarm(self.state,due))
        self.clock+=dt.timedelta(days=1)
        self.assertNotIn((0,1),s.enabled_times(self.state,self.clock))
    def test_missed_once_retires_without_catch_up(self):
        alarm=s.create_alarm(self.state,{'time':'21:00','mode':'once'})
        self.clock=self.clock.replace(hour=21,minute=1)
        self.assertNotIn((21,0),s.enabled_times(self.state,self.clock))
        self.assertEqual(self.alarm(alarm)['last_status'],'missed')
        self.assertFalse(self.alarm(alarm)['enabled'])
        self.clock+=dt.timedelta(days=1)
        self.assertNotIn((21,0),s.enabled_times(self.state,self.clock))
    def test_uncertain_failure_does_not_rearm_and_enable_is_explicit(self):
        alarm=s.create_alarm(self.state,{'time':'21:00','mode':'once'})
        due=self.clock.replace(hour=21,minute=0)
        self.clock=due+dt.timedelta(seconds=1)
        claimed=s.claim_alarm(self.state,due)
        s.finish_alarm(self.state,claimed,'failed')
        self.assertFalse(self.alarm(alarm)['enabled'])
        s.update_alarm(self.state,alarm,{'enabled':True})
        self.assertTrue(self.alarm(alarm)['enabled'])
        self.assertEqual(self.alarm(alarm)['run_at'],(due+dt.timedelta(days=1)).isoformat())
    def test_edit_during_send_does_not_get_overwritten_by_old_result(self):
        alarm=s.create_alarm(self.state,{'time':'21:00','mode':'once'})
        due=self.clock.replace(hour=21,minute=0)
        self.clock=due+dt.timedelta(seconds=1)
        claimed=s.claim_alarm(self.state,due)
        s.update_alarm(self.state,alarm,{'time':'21:05','mode':'once','enabled':True})
        s.finish_alarm(self.state,claimed,'completed')
        actual=self.alarm(alarm)
        self.assertEqual(actual['time'],'21:05')
        self.assertTrue(actual['enabled'])
        self.assertIsNone(actual['last_status'])
    def test_legacy_database_migration_preserves_original_alarm(self):
        db=sqlite3.connect(self.state/'runs.sqlite')
        db.execute('CREATE TABLE alarm_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO alarm_settings VALUES('initialized','1')")
        db.execute('CREATE TABLE alarms(id TEXT PRIMARY KEY,time TEXT UNIQUE,enabled INTEGER,created_at TEXT)')
        db.execute("INSERT INTO alarms VALUES('original-id','07:00',1,'original-created')")
        db.commit();db.close()
        actual=s.list_alarms(self.state)
        self.assertEqual(len(actual),1)
        self.assertEqual((actual[0]['id'],actual[0]['time'],actual[0]['mode']),('original-id','07:00','daily'))
    def test_patch_fixed_inference_settings_is_rejected(self):
        alarm=s.list_alarms(self.state)[0]['id']
        for field in ['model','effort','message','run_at']:
            with self.assertRaises(ValueError):
                s.update_alarm(self.state,alarm,{field:'changed'})

if __name__=='__main__':unittest.main()
