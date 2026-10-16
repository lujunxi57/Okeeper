"""Account routing without changing the desktop server's login or process."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

from account_quota import SavedTokens, claims, discover_accounts
from rpc import BIN, RPC


def account_catalog():
    records, _ = discover_accounts()
    return [{'id': r['id'], 'name': r['label'], 'current': r['current']} for r in records]


def find_account(account_id):
    records, _ = discover_accounts()
    return next((r for r in records if r['id'] == account_id), None)


def matches(rpc, record):
    result = rpc.call('account/read', {'refreshToken': False})
    account = result.get('account') or {}
    routing = result.get('workspaceRouting') or {}
    return (account.get('type') == 'chatgpt'
            and routing.get('chatgptAccountId') == record['key'][0]
            and (account.get('email') or '').casefold() == (record['key'][2] or '').casefold()
            and bool(record['key'][2]))


class IsolatedRPC(RPC):
    def __init__(self, home, record):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        self.record = record
        self.tokens = SavedTokens()
        self.messages = queue.Queue()
        self.seq = 0
        self.events = []
        self.process = None
        # Explicit config/env isolation includes SQLite and credential storage.
        env = {k: v for k, v in os.environ.items() if k not in {
            'CODEX_HOME', 'CODEX_SQLITE_HOME', 'CODEX_ACCESS_TOKEN', 'CODEX_API_KEY',
            'OPENAI_API_KEY', 'OPENAI_BASE_URL'}}
        env['CODEX_HOME'] = str(self.home)
        command = [str(Path(BIN).resolve()), 'app-server', '--stdio',
                   '-c', 'cli_auth_credentials_store="ephemeral"',
                   '-c', 'model_provider="openai"', '-c', 'model="gpt-6-luna"',
                   '-c', 'model_reasoning_effort="low"', '-c', 'approval_policy="never"',
                   '-c', 'sandbox_mode="read-only"']
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
            threading.Thread(target=self._read, daemon=True).start()
            self.call('initialize', {'clientInfo': {'name': 'okeeper_account', 'title': 'Okeeper account trigger', 'version': '1.0'},
                                     'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized'})
            bundle = self.bundle()
            try:self.call('account/login/start', {'type': 'chatgptAuthTokens', **bundle})
            except RuntimeError as error:
                if '401' not in str(error) and 'unauthorized' not in str(error).lower():raise
                # Token revocation may precede JWT expiry. No turn has started;
                # refresh the same saved account, then retry authentication once.
                bundle=self.bundle(force=True)
                self.call('account/login/start',{'type':'chatgptAuthTokens',**bundle})
            if not matches(self, record): raise RuntimeError('独立后台账号核验失败')
        except Exception:
            self.close()
            raise

    def bundle(self,force=False):
        access = (self.record['auth'].get('tokens') or {}).get('access_token') if self.record['current'] else None
        if force or not access or claims(access).get('exp', 0) < __import__('time').time() + 90:
            access = self.tokens.access(self.record,force=force)
        plan = claims(access).get('https://api.openai.com/auth', {}).get('chatgpt_plan_type')
        return {'accessToken': access, 'chatgptAccountId': self.record['key'][0], 'chatgptPlanType': plan}

    def _read(self):
        try:
            for line in self.process.stdout:
                try: self.messages.put(json.loads(line))
                except ValueError: pass
        finally:
            self.messages.put(None)

    def send(self, msg):
        self.process.stdin.write(json.dumps(msg) + '\n')
        self.process.stdin.flush()

    def recv(self, timeout=10):
        while True:
            try: value = self.messages.get(timeout=timeout)
            except queue.Empty: raise TimeoutError('独立后台响应超时') from None
            if value is None: raise ConnectionError('独立后台已断开')
            if value.get('method') == 'account/chatgptAuthTokens/refresh' and 'id' in value:
                try: self.send({'id': value['id'], 'result': self.bundle(force=True)})
                except Exception: self.send({'id': value['id'], 'error': {'code': -32000, 'message': 'Account credential unavailable'}})
                continue
            return value

    def close(self):
        if self.process:
            if self.process.stdin and not self.process.stdin.closed: self.process.stdin.close()
            try: self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            if self.process.stdout: self.process.stdout.close()


def open_account(state, record):
    main = None
    try:
        main = RPC()
        if matches(main, record): return main, 'main'
    except Exception:
        pass
    if main: main.close()
    home = Path(state) / 'account-homes' / record['id']
    return IsolatedRPC(home, record), 'isolated'
