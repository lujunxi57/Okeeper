import datetime as dt
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
import trigger as t

class FakeRPC:
    def __init__(self, clock, active=False, flip_active=False):
        self.clock = clock
        self.active = active
        self.flip_active = flip_active
        self.calls = []
        self.reads = 0
        self.busy = set()
        self.inventory = {'old-chat', 'busy-old', 'future-chat', 'new-chat'}
        self.archived = set()
    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'account/rateLimits/read':
            raise AssertionError('Quota windows must no longer be queried')
        if method == 'thread/list':
            return {'data': [{'id': i, 'status': {'type': 'active' if i in self.busy else 'idle'}}
                             for i in self.inventory if (i in self.archived) == params['archived']], 'nextCursor': None}
        if method == 'thread/archive':
            self.archived.add(params['threadId'])
            return {}
        if method == 'thread/start':
            return {'model': 'gpt-6-luna', 'reasoningEffort': 'low', 'thread': {'id': 'new-chat'}}
        if method == 'thread/unarchive':
            self.archived.discard(params['threadId'])
            return {'thread': {'id':params['threadId'],'status':{'type':'idle'}}}
        if method == 'thread/read':
            return {'thread': {'id': params['threadId'], 'path':'/home/test/.codex/archived_sessions/rollout.jsonl' if params['threadId'] in self.archived else '/home/test/.codex/sessions/rollout.jsonl', 'status': {'type': 'active' if params['threadId'] in self.busy else 'idle'},
                               'turns': [{'id': 'previous'}]}}
        if method == 'thread/resume':
            return {'thread': {'id': params['threadId'], 'status': {'type': 'idle'}}}
        if method in ('thread/name/set', 'thread/delete'):
            return {}
        raise AssertionError(method)
    def close(self):
        pass

class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        (self.state / 'daily-chat-test-passed.json').write_text('{}')
        self.clock = dt.datetime(2026, 10, 10, 7, 0, 1, tzinfo=t.TZ)
        self.rpc = FakeRPC(self.clock)
        self.sends = []
        self.records = []
        def turn(rpc, thread, key, scheduled):
            self.sends.append(thread)
            return {'turn_id': 'fake-completed'}
        self.patches = [patch.object(t, 'STATE', self.state), patch.object(t, 'now', lambda: self.clock),
                        patch.object(t, 'RPC', lambda: self.rpc), patch.object(t, 'run_turn', turn),
                        patch.object(t, 'log', lambda event, **fields: self.records.append((event, fields))),
                        patch('schedule_store.now', lambda: self.clock),
                        patch.object(sys, 'argv', ['trigger.py', 'daily'])]
        for p in self.patches:
            p.start()
        self.db = sqlite3.connect(self.state / 'runs.sqlite')
        self.db.execute('CREATE TABLE daily_chats (day TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created_at TEXT NOT NULL)')
        self.db.commit()
    def tearDown(self):
        self.db.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()
    def step(self, hour, minute, active=False):
        self.clock = self.clock.replace(hour=hour, minute=minute, second=1)
        self.rpc.clock = self.clock
        self.rpc.active = active
        t.main()
    def test_all_three_slots_trigger_and_create_only_once(self):
        self.step(7, 0, True)
        self.step(12, 5, True)
        self.step(17, 10, True)
        self.assertEqual(self.sends, ['new-chat', 'new-chat', 'new-chat'])
        self.assertEqual(sum(m == 'thread/start' for m, p in self.rpc.calls), 1)
        self.assertFalse(any(m == 'account/rateLimits/read' for m, p in self.rpc.calls))
    def test_evening_can_be_first_chat(self):
        self.step(17, 10)
        self.assertEqual(self.sends, ['new-chat'])
    def test_creation_deletes_only_registered_past_chats_and_defers_busy(self):
        self.db.executemany('INSERT INTO daily_chats VALUES (?,?,?)', [
            ('2026-10-08', 'old-chat', ''), ('2026-10-09', 'busy-old', ''), ('2026-10-11', 'future-chat', '')])
        self.db.commit()
        self.rpc.busy.add('busy-old')
        self.step(12, 5)
        deleted = [p['threadId'] for m, p in self.rpc.calls if m == 'thread/delete']
        self.assertEqual(deleted, ['old-chat'])
        lifecycle = [(m, p['threadId']) for m, p in self.rpc.calls if m in ('thread/archive', 'thread/delete')]
        self.assertEqual(lifecycle, [('thread/archive', 'old-chat'), ('thread/delete', 'old-chat')])
        self.assertEqual(set(r[0] for r in self.db.execute('SELECT thread_id FROM daily_chats')), {'busy-old', 'future-chat', 'new-chat'})
    def test_late_run_and_duplicate_never_send(self):
        self.clock = self.clock.replace(second=30)
        t.main()
        self.assertEqual(self.sends, [])
        self.clock = self.clock.replace(second=1)
        t.main()
        self.assertEqual(self.sends, [])
        self.assertIn('skipped_duplicate', [event for event, fields in self.records])

    def test_already_archived_chat_is_deleted(self):
        self.db.execute('INSERT INTO daily_chats VALUES (?,?,?)', ('2026-10-09', 'old-chat', ''))
        self.db.commit()
        self.rpc.archived.add('old-chat')
        self.step(12, 5)
        lifecycle = [m for m, p in self.rpc.calls if m in ('thread/archive', 'thread/delete')]
        self.assertEqual(lifecycle, ['thread/delete'])
    def test_stale_registry_entry_removed_after_complete_inventory(self):
        self.db.execute('INSERT INTO daily_chats VALUES (?,?,?)', ('2026-10-09', 'missing-chat', ''))
        self.db.commit()
        self.step(12, 5)
        self.assertIsNone(self.db.execute('SELECT * FROM daily_chats WHERE thread_id=?', ('missing-chat',)).fetchone())
        self.assertIn('old_chat_already_absent', [event for event, fields in self.records])
    def test_archive_failure_prevents_delete_and_keeps_registration(self):
        self.db.execute('INSERT INTO daily_chats VALUES (?,?,?)', ('2026-10-09', 'old-chat', ''))
        self.db.commit()
        original = self.rpc.call
        def call(method, params):
            if method == 'thread/archive':
                raise RuntimeError('archive failed')
            return original(method, params)
        self.rpc.call = call
        self.step(12, 5)
        self.assertFalse(any(m == 'thread/delete' for m, p in self.rpc.calls))
        self.assertIsNotNone(self.db.execute('SELECT * FROM daily_chats WHERE thread_id=?', ('old-chat',)).fetchone())

    def test_custom_alarm_drives_dispatch_and_disabled_alarm_does_not(self):
        from schedule_store import create_alarm, toggle_alarm
        create_alarm(self.state, {'time': '08:23'})
        self.step(8, 23)
        self.assertEqual(self.sends, ['new-chat'])
        alarm = create_alarm(self.state, {'time': '08:24'})
        toggle_alarm(self.state, alarm, {'enabled': False})
        self.step(8, 24)
        self.assertEqual(self.sends, ['new-chat'])
    def test_deleting_all_alarms_stops_dispatch_and_does_not_reseed(self):
        from schedule_store import list_alarms, delete_alarm, enabled_times
        for alarm in list_alarms(self.state):
            delete_alarm(self.state, alarm['id'])
        self.step(7, 0)
        self.assertEqual(self.sends, [])
        self.assertEqual(enabled_times(self.state), ())

    def test_one_shot_is_disarmed_before_processing_and_never_repeats(self):
        from schedule_store import create_alarm, list_alarms
        alarm = create_alarm(self.state, {'time':'08:23','mode':'once'})
        self.step(8,23)
        self.assertEqual(self.sends,['new-chat'])
        actual=next(a for a in list_alarms(self.state) if a['id']==alarm)
        self.assertFalse(actual['enabled'])
        self.assertEqual(actual['last_status'],'completed')
        self.clock += dt.timedelta(days=1)
        self.step(8,23)
        self.assertEqual(self.sends,['new-chat'])

    def test_archived_current_day_chat_is_restored_and_reused(self):
        from schedule_store import create_alarm
        create_alarm(self.state, {'time':'08:23'})
        self.db.execute('INSERT INTO daily_chats VALUES (?,?,?)',(self.clock.date().isoformat(),'old-chat',''))
        self.db.commit()
        self.rpc.archived.add('old-chat')
        self.step(8,23)
        self.assertEqual(self.sends,['old-chat'])
        self.assertNotIn('old-chat',self.rpc.archived)
        self.assertFalse(any(m=='thread/start' for m,p in self.rpc.calls))
        self.assertIn('current_chat_unarchived',[event for event,fields in self.records])

if __name__ == '__main__':
    unittest.main()
