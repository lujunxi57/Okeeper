import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from account_quota import atomic_json
from request_history import recent_requests
from schedule_store import connect, database
from turn_result import FAILURE_MESSAGES


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        connect(self.state).close()
        self.clock = dt.datetime(2026, 10, 19, 8, tzinfo=dt.timezone.utc).timestamp()

    def account(self, slot='account-v1:a:2026-10-19T15:00:00+08:00', **fields):
        row = {'slot': slot, 'alarm_id': '', 'revision': 0, 'account_id': 'a' * 20,
               'account_name': 'alice@example.com', 'scheduled': '2026-10-19T15:00:00+08:00',
               'status': 'completed', 'at': '2026-10-19T15:00:02+08:00', 'route': 'isolated'}
        row.update(fields)
        with database(self.state) as db:
            db.execute('INSERT INTO account_runs (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))

    def continuation(self, name='continue', **fields):
        row = {'at': '2026-10-19T07:01:00+00:00', 'thread_id': 'original',
               'status': 'accepted', 'operation_id': 'operation', 'turn_id': 'receipt', **fields}
        atomic_json(self.state / 'account-continue-receipts' / (name + '.json'), row)

    def test_combines_sources_by_absolute_time_and_preserves_continue_acceptance(self):
        self.account()
        self.account(slot='account-manual-v1:a:request', scheduled='2026-10-19T15:02:00+08:00', route='main')
        self.continuation(account_name='bob@example.com')
        with database(self.state) as db:
            db.execute("INSERT INTO runs(slot,status,at) VALUES('daily-v2:2026-10-19T14:58:00+08:00','verified_completed','2026-10-19T14:58:02+08:00')")
            db.execute("INSERT INTO custom_runs(slot,alarm_id,revision,thread_id,scheduled,prompt,status,at,thread_name) VALUES('custom','deleted',0,'chat','2026-10-19T06:59:00+00:00','original prompt','completed','2026-10-19T07:00:30+00:00','Original title')")
        view = recent_requests(self.state, self.clock)
        self.assertEqual([r['kind'] for r in view['requests']], ['manual', 'continue', 'automatic', 'custom', 'automatic'])
        continued = view['requests'][1]
        self.assertEqual((continued['status'], continued['status_label'], continued['message']), ('accepted', '已提交', 'continue'))
        custom = view['requests'][3]
        self.assertEqual((custom['thread_name'], custom['message']), ('Original title', 'original prompt'))
        self.assertFalse(view['partial'])

    def test_reading_does_not_repair_stale_receipts_or_change_database(self):
        self.account(status='sending')
        before = (self.state / 'runs.sqlite').read_bytes()
        first = recent_requests(self.state, self.clock)
        second = recent_requests(self.state, self.clock)
        self.assertEqual(first, second)
        self.assertEqual(first['requests'][0]['status'], 'uncertain')
        self.assertEqual(before, (self.state / 'runs.sqlite').read_bytes())
        with sqlite3.connect(self.state / 'runs.sqlite') as db:
            self.assertEqual(db.execute('SELECT status FROM account_runs').fetchone()[0], 'sending')

    def test_known_pre_submit_settings_failure_shows_specific_reason_without_rewriting_history(self):
        with database(self.state) as db:
            db.execute("INSERT INTO custom_runs(slot,alarm_id,revision,thread_id,scheduled,prompt,status,at,error,route) VALUES('custom','deleted',0,'chat','2026-10-24T05:34:00+08:00','1','failed_or_uncertain','2026-10-24T05:34:03+08:00',?,'main')",('无法确认会话当前的模型和思考强度，未发送',))
        before=(self.state/'runs.sqlite').read_bytes()
        record=recent_requests(self.state,self.clock)['requests'][0]
        self.assertEqual((record['status'],record['status_label']),('skipped','未发送'))
        self.assertEqual(record['result'],'无法确认会话当前的模型和思考强度，未发送。')
        self.assertEqual(before,(self.state/'runs.sqlite').read_bytes())
        with database(self.state) as db:db.execute("UPDATE custom_runs SET turn_id='accepted-turn' WHERE slot='custom'")
        record=recent_requests(self.state,self.clock)['requests'][0]
        self.assertEqual(record['status'],'uncertain')

    def test_account_failure_shows_only_fixed_safe_reasons_and_keeps_history(self):
        for code,reason in FAILURE_MESSAGES.items():
            self.account(slot=code,status='failed_or_uncertain',turn_id='accepted',error=reason)
        self.account(slot='private',status='failed_or_uncertain',error='private credential diagnostic')
        before=(self.state/'runs.sqlite').read_bytes()
        records=recent_requests(self.state,self.clock)['requests']
        for code,reason in FAILURE_MESSAGES.items():
            record=next(r for r in records if r['id']=='account:'+code)
            self.assertEqual(record['result'],reason+'。')
            self.assertEqual(record['turn_id'],'accepted')
        self.assertNotIn('private credential',json.dumps(records))
        self.assertEqual(before,(self.state/'runs.sqlite').read_bytes())

    def test_never_guesses_historical_account_or_unselected_route(self):
        self.account(status='skipped_changed', route=None)
        self.continuation()
        with database(self.state) as db:
            db.execute("INSERT INTO runs(slot,status,at) VALUES('daily-v2:2026-10-19T14:58:00+08:00','failed_or_uncertain','2026-10-19T14:58:02+08:00')")
        records = recent_requests(self.state, self.clock)['requests']
        self.assertEqual(records[0]['account_name'], '')
        self.assertIsNone(records[1]['route'])
        self.assertEqual(records[2]['account_name'], '')
        self.assertIsNone(records[2]['route'])

    def test_old_continue_account_comes_from_its_operation_and_raw_fields_are_not_exposed(self):
        self.continuation(access_token='secret', error='private error')
        atomic_json(self.state / 'account-control-receipts' / 'request.json',
                    {'id': 'operation', 'account_id': 'b' * 20, 'account': 'bob@example.com', 'diagnostic': 'private'})
        record = recent_requests(self.state, self.clock)['requests'][0]
        self.assertEqual((record['account_id'], record['account_name']), ('b' * 20, 'bob@example.com'))
        serialized = json.dumps(record)
        for private in ['secret', 'access_token', 'private error', 'diagnostic']:
            self.assertNotIn(private, serialized)

    def test_corrupt_receipt_is_reported_without_hiding_good_records(self):
        self.account()
        self.continuation()
        (self.state / 'account-continue-receipts' / 'bad.json').write_text('{')
        (self.state / 'account-continue-receipts' / 'invalid.json').write_text(json.dumps({'status': [], 'at': 'invalid', 'thread_id': 'chat'}))
        view = recent_requests(self.state, self.clock)
        self.assertEqual(len(view['requests']), 2)
        self.assertTrue(view['partial'])

    def test_limit_and_exclusion_of_internal_test_runs(self):
        for minute in range(105):
            stamp = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=minute)
            self.account(slot=f'account:{minute}', scheduled=stamp.isoformat())
        with database(self.state) as db:
            db.execute("INSERT INTO runs(slot,status,at) VALUES('test-v2:2026-10-19T16:00:00+08:00','verified_completed','2026-10-19T16:00:00+08:00')")
        view = recent_requests(self.state, self.clock)
        self.assertEqual(len(view['requests']), 100)
        self.assertEqual(view['requests'][0]['id'], 'account:account:104')
        self.assertEqual(view['requests'][-1]['id'], 'account:account:5')

    def test_missing_state_stays_missing_and_returns_empty(self):
        missing = self.state / 'not-created'
        self.assertEqual(recent_requests(missing)['requests'], [])
        self.assertFalse(missing.exists())

    def test_reading_old_schema_requires_no_migration(self):
        other = self.state / 'legacy'; other.mkdir()
        with sqlite3.connect(other / 'runs.sqlite') as db:
            db.execute('CREATE TABLE runs(slot TEXT,status TEXT,at TEXT,turn_id TEXT)')
            db.execute("INSERT INTO runs VALUES('daily-v2:2026-10-19T07:00:00+08:00','verified_completed','2026-10-19T07:00:02+08:00','turn')")
        before = (other / 'runs.sqlite').read_bytes()
        record = recent_requests(other, self.clock)['requests'][0]
        self.assertEqual(record['route'], 'main')
        self.assertEqual(record['account_name'], '')
        self.assertEqual(before, (other / 'runs.sqlite').read_bytes())
