import os
import base64
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

import account_quota as quota


def jwt(data):
    return 'x.' + base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=') + '.x'


def auth(account, email=None, workspace=None):
    email = email or account + '@example.com'
    return {'tokens': {'account_id': workspace or account, 'id_token': jwt({
        'sub': account, 'email': email, 'https://api.openai.com/auth': {'chatgpt_account_id': workspace or account}}),
        'access_token': jwt({'exp': time.time()+3600}), 'refresh_token': 'refresh-'+account}}


def usage(used=20):
    return {'plan_type': 'plus', 'rate_limit': {'primary_window': {
        'used_percent': used, 'limit_window_seconds': 18000, 'reset_at': 1792171786}}}


class MultiAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, filename in [('AUTH', 'auth.json'), ('CC_STORE', 'cc.json'),
                               ('CC_DATABASE', 'cc.db'), ('TOKEN_STATE', 'access.json')]:
            p = patch.object(quota, name, root / filename); p.start(); self.addCleanup(p.stop)
        p = patch.object(quota, 'cc_switch_running', return_value=False); p.start(); self.addCleanup(p.stop)
        self.a, self.b = auth('alice'), auth('bob')
        quota.AUTH.write_text(json.dumps(self.a))
        accounts = {}
        for name, a in [('local-a', self.a), ('local-b', self.b)]:
            t = a['tokens']; accounts[name] = {'account_id': name, 'chatgpt_account_id': t['account_id'],
                'id_token': t['id_token'], 'email': name+'@example.com', 'refresh_token': t['refresh_token'],
                'authenticated_at': 1, 'unknown_field': 'preserve'}
        self.store = {'version': 2, 'accounts': accounts, 'default_account_id': 'local-a', 'future_field': True}
        quota.CC_STORE.write_text(json.dumps(self.store))
        with closing(sqlite3.connect(quota.CC_DATABASE)) as db, db:
            db.execute('CREATE TABLE providers(app_type TEXT, settings_config TEXT)')
            db.execute('INSERT INTO providers VALUES (?,?)', ('codex', json.dumps({'auth': self.a})))

    def record_b(self):
        records, warning = quota.discover_accounts(); self.assertIsNone(warning)
        return next(r for r in records if not r['current'])

    def test_discovery_merges_three_sources_without_duplicate_current_account(self):
        records, warning = quota.discover_accounts()
        self.assertIsNone(warning); self.assertEqual(len(records), 2)
        self.assertTrue(records[0]['current']); self.assertEqual(records[1]['managed_id'], 'local-b')
        self.assertEqual(records[0]['label'], 'alice@example.com')

    def test_same_workspace_different_users_are_distinct_accounts(self):
        quota.AUTH.write_text(json.dumps(auth('someone-else', workspace='bob')))
        records, _ = quota.discover_accounts()
        self.assertEqual(len(records), 3)

    def test_saved_refresh_updates_only_target_and_survives_restart(self):
        record = self.record_b(); before = quota.AUTH.read_bytes()
        refreshed = {'access_token': self.b['tokens']['access_token'], 'id_token': self.b['tokens']['id_token'],
                     'refresh_token': 'rotated-b', 'expires_in': 3600}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _): return json.dumps(refreshed).encode()
        with patch.object(quota, 'urlopen', return_value=Response()) as request:
            access = quota.SavedTokens().access(record)
            self.assertEqual(quota.SavedTokens().access(record), access)
            self.assertEqual(request.call_count, 1)
            req = request.call_args.args[0]
            self.assertEqual(req.full_url, 'https://auth.openai.com/oauth/token')
            self.assertEqual(parse_qs(req.data.decode())['grant_type'], ['refresh_token'])
        after = json.loads(quota.CC_STORE.read_text())
        self.assertEqual(after['accounts']['local-a'], self.store['accounts']['local-a'])
        self.assertEqual(after['default_account_id'], 'local-a'); self.assertTrue(after['future_field'])
        self.assertEqual(after['accounts']['local-b']['refresh_token'], 'rotated-b')
        self.assertEqual(quota.AUTH.read_bytes(), before)
        self.assertEqual(quota.CC_STORE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(quota.TOKEN_STATE.stat().st_mode & 0o777, 0o600)

    def test_open_cc_switch_never_competes_with_its_refresh_generation(self):
        with patch.object(quota, 'cc_switch_running', return_value=True), patch.object(quota, 'urlopen') as request:
            with self.assertRaises(quota.LoginRequired): quota.SavedTokens().access(self.record_b())
            request.assert_not_called()

    def test_forced_refresh_bypasses_access_token_with_future_expiry(self):
        record=self.record_b()
        record['alternate_auth']=self.b
        with patch.object(quota,'cc_switch_running',return_value=True):
            self.assertEqual(quota.SavedTokens().access(record),self.b['tokens']['access_token'])
            with self.assertRaises(quota.LoginRequired):quota.SavedTokens().access(record,force=True)

    def test_concurrent_credential_change_is_not_overwritten(self):
        record = self.record_b()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(inner, _):
                changed = json.loads(quota.CC_STORE.read_text())
                changed['accounts']['local-b']['refresh_token'] = 'newer-generation'
                quota.CC_STORE.write_text(json.dumps(changed))
                return json.dumps({'access_token': self.b['tokens']['access_token'], 'refresh_token': 'older-result'}).encode()
        with patch.object(quota, 'urlopen', return_value=Response()):
            with self.assertRaises(ValueError): quota.SavedTokens().access(record)
        self.assertEqual(json.loads(quota.CC_STORE.read_text())['accounts']['local-b']['refresh_token'], 'newer-generation')

    def test_one_invalid_login_does_not_substitute_current_account_or_hide_others(self):
        cache = quota.AllQuotas()
        with patch.object(cache.live, 'get', return_value=quota.normalize(usage(), 'al•••@example.com')), \
             patch.object(cache.saved_tokens, 'access', side_effect=quota.LoginRequired('请在 CC Switch 重新登录此账号')):
            result = cache.get()
        self.assertEqual(len(result['accounts']), 2)
        self.assertEqual(result['accounts'][0]['windows'][0]['remaining_percent'], 80)
        self.assertEqual(result['accounts'][1]['windows'], [])
        self.assertTrue(result['accounts'][1]['login_required'])
        self.assertNotIn('refresh-bob', json.dumps(result)); self.assertNotIn('access_token', json.dumps(result))

    def test_account_removed_during_fetch_is_not_returned(self):
        cache = quota.AllQuotas()
        def remove(*_):
            store = json.loads(quota.CC_STORE.read_text()); del store['accounts']['local-b']
            quota.CC_STORE.write_text(json.dumps(store)); return usage()
        with patch.object(cache.live, 'get', return_value=quota.normalize(usage(), 'al•••@example.com')), \
             patch.object(cache.saved_tokens, 'access', return_value='access'), patch.object(quota, 'fetch_usage', side_effect=remove):
            result = cache.get()
        self.assertEqual(len(result['accounts']), 1)

    def test_no_accounts_returns_explicit_empty_state(self):
        quota.AUTH.unlink(); quota.CC_STORE.unlink()
        with closing(sqlite3.connect(quota.CC_DATABASE)) as db, db: db.execute('DELETE FROM providers')
        result = quota.AllQuotas().get()
        self.assertEqual(result['accounts'], []); self.assertTrue(result['error'])


if __name__ == '__main__': unittest.main()
