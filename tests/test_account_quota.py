import os
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_quota as quota


def auth(account='a', email='person@example.com'):
    token = base64.urlsafe_b64encode(json.dumps({'email': email, 'sub': account}).encode()).decode().rstrip('=')
    return {'tokens': {'account_id': account, 'id_token': 'x.' + token + '.x', 'access_token': 'secret-token'}}


def usage(used=70):
    return {'plan_type': 'plus', 'rate_limit': {
        'primary_window': {'used_percent': used, 'limit_window_seconds': 18000, 'reset_at': 1792171786},
        'secondary_window': {'used_percent': 69, 'limit_window_seconds': 604800, 'reset_at': 1792649765}}}


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'auth.json'
        self.path.write_text(json.dumps(auth()))
        p = patch.object(quota, 'AUTH', self.path); p.start(); self.addCleanup(p.stop)

    def test_remaining_is_not_used_and_preserves_official_reset(self):
        data = quota.normalize(usage(), '账号')
        self.assertEqual([w['remaining_percent'] for w in data['windows']], [30, 31])
        self.assertEqual(data['windows'][0]['resets_at'], 1792171786)
        self.assertEqual([w['window_seconds'] for w in data['windows']], [18000, 604800])

    def test_zero_is_valid_but_missing_and_nonfinite_are_not_fabricated(self):
        self.assertEqual(quota.normalize(usage(0), '账号')['windows'][0]['remaining_percent'], 100)
        for value in (None, float('nan'), True, '20'):
            self.assertEqual(len(quota.normalize(usage(value), '账号')['windows']), 1)
        with self.assertRaises(ValueError): quota.normalize({'rate_limit': {}}, '账号')

    @patch.object(quota, 'read_usage', return_value=usage())
    def test_cache_and_refresh_are_bounded_and_no_credentials_returned(self, read):
        cache = quota.QuotaCache()
        first = cache.get(); cache.get(force=True); cache.get()
        self.assertEqual(read.call_count, 1)
        self.assertNotIn('secret-token', json.dumps(first))
        self.assertEqual(first['account'], 'person@example.com')
        first['windows'][0]['remaining_percent'] = 999
        self.assertEqual(cache.get()['windows'][0]['remaining_percent'], 30)

    def test_failed_refresh_keeps_timestamp_and_marks_stale(self):
        cache = quota.QuotaCache()
        with patch.object(quota, 'read_usage', return_value=usage()): first = cache.get()
        cache.attempted -= 61
        with patch.object(quota, 'read_usage', side_effect=RuntimeError('secret-token')): last = cache.get()
        self.assertTrue(last['stale']); self.assertEqual(last['updated_at'], first['updated_at'])
        self.assertNotIn('secret-token', json.dumps(last))

    def test_switch_or_logout_never_reuses_previous_account_quota(self):
        cache = quota.QuotaCache()
        with patch.object(quota, 'read_usage', return_value=usage()): cache.get()
        self.path.write_text(json.dumps(auth('b', 'other@example.org')))
        with patch.object(quota, 'read_usage', side_effect=OSError()): data = cache.get()
        self.assertEqual(data['windows'], []); self.assertFalse(data['stale'])
        self.path.unlink(); self.assertEqual(cache.get()['windows'], [])

    def test_switch_during_fetch_discards_response(self):
        cache = quota.QuotaCache()
        def switch(*_):
            self.path.write_text(json.dumps(auth('b')))
            return usage()
        with patch.object(quota, 'read_usage', side_effect=switch): data = cache.get()
        self.assertEqual(data['windows'], [])

    def test_cc_switch_url_headers_and_read_only_credentials(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _): return json.dumps(usage()).encode()
        with patch.object(quota, 'urlopen', return_value=Response()) as request:
            quota.read_usage(auth(), ('a', None, ''))
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, 'https://chatgpt.com/backend-api/wham/usage')
        self.assertEqual(req.get_method(), 'GET')
        self.assertEqual(req.get_header('Chatgpt-account-id'), 'a')
        self.assertEqual(json.loads(self.path.read_text()), auth())

    def test_rpc_fallback_refuses_a_different_account(self):
        with patch.object(quota, 'urlopen', side_effect=OSError()), patch.object(quota, 'RPC') as rpc:
            rpc.return_value.call.return_value = {'accountId': 'b', 'rateLimits': {}}
            with self.assertRaises(ValueError): quota.read_usage(auth(), ('a', None, ''))
            rpc.return_value.close.assert_called_once()

    def test_rpc_fallback_normalizes_windows_without_inference(self):
        result = {'accountId': 'a', 'rateLimits': {'planType': 'plus', 'primary': {
            'usedPercent': 12, 'windowDurationMins': 300, 'resetsAt': 1792171786}}}
        with patch.object(quota, 'urlopen', side_effect=OSError()), patch.object(quota, 'RPC') as rpc:
            rpc.return_value.call.return_value = result
            data = quota.normalize(quota.read_usage(auth(), ('a', None, '')), '账号')
            rpc.return_value.call.assert_called_once_with('account/rateLimits/read', {})
            self.assertEqual(data['windows'][0]['remaining_percent'], 88)


if __name__ == '__main__': unittest.main()
