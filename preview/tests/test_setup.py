import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('okeeper_setup', ROOT / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
spec = importlib.util.spec_from_file_location('fake_codex', Path(__file__).with_name('fake_codex.py'))
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'original'
        self.source.mkdir()
        self.native = self.source / 'auth.json'
        self.native.write_text(json.dumps(fake.auth('first@example.com')))
        self.original_hash = hashlib.sha256(self.native.read_bytes()).hexdigest()
        self.accounts = app.Accounts(self.root / 'managed')
        self.setup = app.Setup(self.accounts, self.source, str(Path(__file__).with_name('fake_codex.py')))

    def tearDown(self):
        self.setup.cancel_login()
        deadline = time.time() + 3
        while list(self.accounts.state.glob('login-*')) and time.time() < deadline:
            time.sleep(.03)
        self.assertEqual(self.original_hash, hashlib.sha256(self.native.read_bytes()).hexdigest())
        self.temp.cleanup()

    def test_import_duplicate_refresh_remove_and_restart(self):
        candidate = self.setup.environment()['candidate']
        self.assertEqual(candidate['email'], 'first@example.com')
        first = self.setup.bind(candidate['id'])
        self.assertEqual(len(first['accounts']), 1)
        self.assertNotIn('tokens', json.dumps(first))
        data = fake.auth('first@example.com')
        data['tokens']['refresh_token'] = 'replacement-fake-refresh'
        self.native.write_text(json.dumps(data))
        self.original_hash = hashlib.sha256(self.native.read_bytes()).hexdigest()
        duplicate = self.setup.bind(candidate['id'])
        self.assertTrue(duplicate['already_bound'])
        self.assertEqual(len(duplicate['accounts']), 1)
        restored = app.Accounts(self.accounts.state)
        self.assertEqual(restored.snapshot()['selected'], candidate['id'])
        with restored.connect() as db:
            saved = json.loads(db.execute('SELECT auth_json FROM accounts').fetchone()[0])
        self.assertEqual(saved['tokens']['refresh_token'], 'replacement-fake-refresh')
        second = self.accounts.add(fake.auth('second@example.com'), 'official_login')
        self.accounts.select(second['added_id'])
        remaining = self.accounts.delete(second['added_id'])
        self.assertEqual(remaining['selected'], candidate['id'])
        self.assertEqual(self.accounts.delete(candidate['id']), {'accounts': [], 'selected': None})
        self.assertNotIn(b'replacement-fake-refresh', self.accounts.path.read_bytes())
        self.assertEqual(self.accounts.path.stat().st_mode & 0o777, 0o600)

    def test_changed_identity_and_missing_login(self):
        old = self.setup.environment()['candidate']['id']
        self.native.write_text(json.dumps(fake.auth('different@example.com')))
        self.original_hash = hashlib.sha256(self.native.read_bytes()).hexdigest()
        with self.assertRaises(app.Problem) as error:
            self.setup.bind(old)
        self.assertEqual(error.exception.status, 409)
        self.assertFalse(self.accounts.snapshot()['accounts'])
        self.native.rename(self.source / 'saved.json')
        self.assertIsNone(self.setup.environment()['candidate'])
        (self.source / 'saved.json').rename(self.native)

    def test_native_login_completion_and_cancel(self):
        signal = self.root / 'authorize'
        os.environ['OKEEPER_TEST_LOGIN_SIGNAL'] = str(signal)
        login = self.setup.start_login()
        self.assertEqual(self.setup.start_login()['id'], login['id'])
        self.wait_status('waiting')
        self.assertFalse(self.accounts.snapshot()['accounts'])
        signal.touch()
        self.wait_status('completed')
        self.assertEqual(self.accounts.snapshot()['accounts'][0]['email'], 'added@example.com')
        self.assertIsNone(self.setup.login_status()['code'])
        signal.unlink()
        self.setup.start_login()
        self.wait_status('waiting')
        self.setup.cancel_login()
        self.assertEqual(self.setup.login_status()['status'], 'cancelled')
        signal.touch()
        time.sleep(.2)
        self.assertEqual(self.setup.login_status()['status'], 'cancelled')
        self.assertEqual(len(self.accounts.snapshot()['accounts']), 1)

    def wait_status(self, status):
        deadline = time.time() + 5
        while time.time() < deadline:
            value = self.setup.login_status()
            if value['status'] == status:
                return
            if value['status'] == 'failed':
                self.fail(str(value))
            time.sleep(.03)
        self.fail(f'Did not reach {status}: {self.setup.login_status()}')

    def test_http_scope_csrf_and_public_fields(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.make_handler(self.setup, '127.0.0.1', 0))
        port = server.server_address[1]
        server.RequestHandlerClass = app.make_handler(self.setup, '127.0.0.1', port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f'http://127.0.0.1:{port}'
        def request(path, method='GET', body=None, headers=None):
            data = json.dumps(body or {}).encode() if method != 'GET' else None
            req = urllib.request.Request(url + path, data=data, method=method, headers=headers or {})
            return opener.open(req, timeout=3)
        with request('/api/bootstrap') as response:
            public = response.read().decode()
            self.assertNotIn('fake-access', public)
            self.assertNotIn('refresh_token', public)
            candidate = json.loads(public)['environment']['candidate']['id']
        with self.assertRaises(urllib.error.HTTPError) as error:
            request('/api/accounts/bind', 'POST', {'candidate_id': candidate})
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError):
            request('/api/accounts/bind', 'POST', {'candidate_id': candidate}, {'Content-Type': 'application/json', 'X-Okeeper-Request': '1', 'Origin': 'https://elsewhere.example'})
        headers = {'Content-Type': 'application/json', 'X-Okeeper-Request': '1'}
        with request('/api/accounts/bind', 'POST', {'candidate_id': candidate}, headers) as response:
            self.assertEqual(response.status, 200)
        for route in ('/accounts.sqlite', '/.git/config', '/server.py', '/../accounts.sqlite'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                request(route)
            self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
