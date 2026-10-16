import sqlite3
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from schedule_store import create_alarm, delete_alarm, enabled_times, list_alarms, snapshot, toggle_alarm

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def test_times_persist_and_duplicate_is_rejected(self):
        alarm = create_alarm(self.state, {'time': '09:37'})
        self.assertIn((9,37), enabled_times(self.state))
        with self.assertRaises(sqlite3.IntegrityError):
            create_alarm(self.state, {'time': '09:37'})
        toggle_alarm(self.state, alarm, {'enabled': False})
        self.assertNotIn((9,37), enabled_times(self.state))
        delete_alarm(self.state, alarm)
        self.assertFalse(any(a['id'] == alarm for a in list_alarms(self.state)))
    def test_model_effort_and_message_cannot_be_changed(self):
        for field, value in [('model','gpt-6-astra'),('effort','high'),('message','different')]:
            with self.assertRaises(ValueError):
                create_alarm(self.state, {'time': '09:37', field: value})
        view = snapshot(self.state)
        self.assertEqual((view['model'],view['effort'],view['message']), ('6 Luna','Light','ok'))
    def test_invalid_times_and_non_boolean_switches_rejected(self):
        for value in ['24:00','-1:30','9:30','09:60','12:00:30',None,930]:
            with self.assertRaises(ValueError):
                create_alarm(self.state, {'time': value})
        for value in [1,0,'true',None]:
            with self.assertRaises(ValueError):
                create_alarm(self.state, {'time':'09:37','enabled':value})
    def test_deleted_list_does_not_restore_defaults_after_restart(self):
        for alarm in list_alarms(self.state):
            delete_alarm(self.state, alarm['id'])
        self.assertEqual(list_alarms(self.state), [])
        self.assertIsNone(snapshot(self.state)['next'])

if __name__ == '__main__':
    unittest.main()
