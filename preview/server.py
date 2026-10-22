#!/usr/bin/env python3
"""Single-user Okeeper setup and account manager for localhost or a trusted tailnet."""
import argparse
import base64
from contextlib import closing
import hashlib
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def claims(value):
    try:
        part = value.split('.')[1]
        data = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        return data if isinstance(data, dict) else {}
    except (ValueError, IndexError, TypeError):
        return {}


def describe_auth(auth):
    tokens = auth.get('tokens') if isinstance(auth, dict) else None
    if not isinstance(tokens, dict) or not all(isinstance(tokens.get(k), str) and tokens[k]
            for k in ('id_token', 'access_token', 'refresh_token', 'account_id')):
        raise Problem('没有可绑定的 Codex 订阅账号，请通过官方页面登录。')
    user = claims(tokens['id_token'])
    email = user.get('email')
    if not isinstance(email, str) or not email:
        raise Problem('登录记录缺少账号身份，请重新登录。')
    key = [tokens['account_id'], user.get('sub'), email.casefold()]
    account_id = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
    plan_claims = user.get('https://api.openai.com/auth')
    plan = plan_claims.get('chatgpt_plan_type') if isinstance(plan_claims, dict) else None
    return {'id': account_id, 'email': email, 'plan': plan if isinstance(plan, str) else None}


def read_auth(path):
    if path.is_symlink():
        raise Problem('登录文件是符号链接，请使用独立登录添加账号。')
    try:
        auth = json.loads(path.read_text())
    except FileNotFoundError:
        raise Problem('尚未找到 Codex 登录记录。') from None
    except (OSError, ValueError):
        raise Problem('无法读取 Codex 登录记录，请重新登录。') from None
    return auth, describe_auth(auth)


def child_env(home):
    env = {k: v for k, v in os.environ.items() if k not in {
        'CODEX_HOME', 'CODEX_SQLITE_HOME', 'CODEX_ACCESS_TOKEN', 'CODEX_API_KEY',
        'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_CONFIG_OVERRIDES'}}
    env['CODEX_HOME'] = str(home)
    return env


class Accounts:
    def __init__(self, state):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.state / 'accounts.sqlite'
        self.lock = threading.RLock()
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, email TEXT, plan TEXT, source TEXT, added_at INTEGER, auth_json TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), selected TEXT)')
            db.execute('INSERT OR IGNORE INTO settings(id,selected) VALUES(1,NULL)')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA secure_delete=ON')
        return db

    def snapshot(self):
        with self.lock, closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            records = [dict(r) for r in db.execute('SELECT id,email,plan,source,added_at FROM accounts ORDER BY added_at,id')]
            selected = db.execute('SELECT selected FROM settings WHERE id=1').fetchone()[0]
            return {'accounts': records, 'selected': selected}

    def add(self, auth, source):
        record = describe_auth(auth)
        with self.lock, closing(self.connect()) as db, db:
            existing = db.execute('SELECT 1 FROM accounts WHERE id=?', (record['id'],)).fetchone()
            if not existing:
                db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?)',
                    (record['id'], record['email'], record['plan'], source, int(time.time()), json.dumps(auth)))
                db.execute('UPDATE settings SET selected=? WHERE id=1 AND selected IS NULL', (record['id'],))
            else:
                db.execute('UPDATE accounts SET plan=?,source=?,auth_json=? WHERE id=?',
                    (record['plan'], source, json.dumps(auth), record['id']))
        return self.snapshot() | {'added_id': record['id'], 'already_bound': bool(existing)}

    def select(self, account_id):
        with self.lock, closing(self.connect()) as db, db:
            if not db.execute('SELECT 1 FROM accounts WHERE id=?', (account_id,)).fetchone():
                raise Problem('这个账号已移除，请刷新列表。', 404)
            db.execute('UPDATE settings SET selected=? WHERE id=1', (account_id,))
        return self.snapshot()

    def delete(self, account_id):
        with self.lock, closing(self.connect()) as db, db:
            if db.execute('DELETE FROM accounts WHERE id=?', (account_id,)).rowcount != 1:
                raise Problem('这个账号已移除，请刷新列表。', 404)
            row = db.execute('SELECT id FROM accounts ORDER BY added_at,id LIMIT 1').fetchone()
            db.execute('UPDATE settings SET selected=? WHERE id=1 AND selected=?', (row[0] if row else None, account_id))
        return self.snapshot()


class NativeClient:
    """An isolated stdio process, never a connection to the user's running daemon."""
    def __init__(self, binary, home):
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        self.process = subprocess.Popen([binary, 'app-server', '--stdio', '-c', 'cli_auth_credentials_store="file"',
            '-c', 'model_provider="openai"', '-c', 'approval_policy="never"', '-c', 'sandbox_mode="read-only"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=child_env(home), cwd=home, **flags)
        self.incoming = queue.Queue()
        self.events = []
        self.close_lock = threading.Lock()
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'okeeper_setup', 'title': 'Okeeper account setup', 'version': '0.1'}})
            self.send({'method': 'initialized'})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.incoming.put(json.loads(line))
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass
        finally:
            self.incoming.put(None)

    def send(self, data):
        self.process.stdin.write(json.dumps(data) + '\n')
        self.process.stdin.flush()

    def receive(self, timeout):
        message = self.incoming.get(timeout=timeout)
        if message is None:
            raise Problem('Codex 登录进程已退出，请重新开始。', 502)
        return message

    def call(self, method, params, timeout=30):
        self.seq += 1
        self.send({'id': self.seq, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.receive(max(.01, deadline - time.monotonic()))
            if message.get('id') == self.seq:
                if 'error' in message:
                    raise Problem('Codex 未能完成登录请求，请检查网络和设备码登录设置后重试。', 502)
                return message.get('result', {})
            self.events.append(message)

    def close(self):
        with self.close_lock:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=4)
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()


class Setup:
    def __init__(self, accounts, source_home=None, binary=None):
        self.accounts = accounts
        self.source_home = Path(source_home or os.environ.get('OKEEPER_SOURCE_CODEX_HOME') or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        self.binary = binary or os.environ.get('OKEEPER_CODEX_BIN') or shutil.which('codex')
        self.login_lock = threading.RLock()
        self.login = None
        self.client = None
        self.cancelled = None
        self.worker = None
        self.version = None
        if self.binary:
            try:
                with tempfile.TemporaryDirectory(dir=accounts.state, prefix='probe-') as home:
                    result = subprocess.run([self.binary, '--version'], capture_output=True, text=True,
                        timeout=5, env=child_env(home), cwd=home)
                self.version = result.stdout.strip() if result.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                self.version = None

    def environment(self):
        candidate = None
        issue = None
        try:
            _, candidate = read_auth(self.source_home / 'auth.json')
        except Problem as error:
            issue = str(error)
        return {'system': platform.system(), 'architecture': platform.machine(),
            'codex_installed': bool(self.binary and self.version), 'codex_version': self.version,
            'candidate': candidate, 'login_note': issue, 'detection': 'local_login_record',
            'inference_verified': False}

    def bind(self, expected_id):
        auth, record = read_auth(self.source_home / 'auth.json')
        if record['id'] != expected_id:
            raise Problem('检测到的登录账号已经变化，请重新检测后确认。', 409)
        return self.accounts.add(auth, 'detected')

    def login_status(self):
        with self.login_lock:
            return dict(self.login) if self.login else {'status': 'idle'}

    def start_login(self):
        if not self.binary or not self.version:
            raise Problem('请先在运行设备安装 Codex，再重新检测。', 409)
        with self.login_lock:
            if self.login and self.login['status'] in ('starting', 'waiting'):
                return dict(self.login)
            self.login = {'id': uuid.uuid4().hex, 'status': 'starting', 'expires_at': int(time.time()) + 600}
            cancel = self.cancelled = threading.Event()
            login_id = self.login['id']
            self.worker = threading.Thread(target=self._login_worker, args=(login_id, cancel), daemon=True)
            self.worker.start()
            return dict(self.login)

    def _publish(self, login_id, **fields):
        with self.login_lock:
            if self.login and self.login['id'] == login_id and self.login['status'] != 'cancelled':
                self.login.update(fields)

    def _login_worker(self, login_id, cancel):
        client = None
        try:
            with tempfile.TemporaryDirectory(dir=self.accounts.state, prefix='login-') as home:
                try:
                    client = NativeClient(self.binary, home)
                    with self.login_lock:
                        if cancel.is_set():
                            return
                        self.client = client
                    result = client.call('account/login/start', {'type': 'chatgptDeviceCode'})
                    url = result.get('verificationUrl', '')
                    if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'auth.openai.com' or not result.get('userCode'):
                        raise Problem('Codex 没有返回可用的官方登录信息，请检查设备码登录设置。', 502)
                    self._publish(login_id, status='waiting', url=url, code=result['userCode'])
                    deadline = time.monotonic() + 600
                    while time.monotonic() < deadline and not cancel.is_set():
                        if client.events:
                            message = client.events.pop(0)
                        else:
                            try:
                                message = client.receive(.5)
                            except queue.Empty:
                                continue
                        if message.get('method') == 'account/login/completed':
                            if not message.get('params', {}).get('success'):
                                raise Problem('登录未完成，请确认官方授权页面后重新开始。')
                            account = client.call('account/read', {'refreshToken': False}).get('account') or {}
                            auth, record = read_auth(Path(home) / 'auth.json')
                            if account.get('type') != 'chatgpt' or (account.get('email') or '').casefold() != record['email'].casefold():
                                raise Problem('登录身份核对未通过，请重新添加。')
                            with self.login_lock:
                                if cancel.is_set():
                                    return
                                added = self.accounts.add(auth, 'official_login')
                                self._publish(login_id, status='completed', account_id=added['added_id'], code=None, url=None)
                            return
                    if not cancel.is_set():
                        self._publish(login_id, status='expired', error='本次登录已过期，请重新生成登录码。', code=None, url=None)
                finally:
                    if client:
                        client.close()
        except (Problem, OSError, ValueError, queue.Empty, subprocess.TimeoutExpired):
            if not cancel.is_set():
                self._publish(login_id, status='failed', error='登录未完成。请检查网络及 ChatGPT 的设备码登录设置，然后重新开始。', code=None, url=None)
        finally:
            with self.login_lock:
                if self.client is client:
                    self.client = None

    def cancel_login(self):
        with self.login_lock:
            if self.cancelled:
                self.cancelled.set()
            if self.client:
                self.client.close()
                self.client = None
            if self.login and self.login['status'] in ('starting', 'waiting'):
                self.login.update(status='cancelled', code=None, url=None)
            worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=6)
        return self.login_status()


PUBLIC = Path(__file__).resolve().parent / 'public'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'), '/main-style.css': ('main-style.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/icon.png': ('icon.png', 'image/png')}


def make_handler(setup, bind, port):
    allowed_hosts = {f'{bind}:{port}', f'127.0.0.1:{port}', f'localhost:{port}'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value, mime='application/json; charset=utf-8'):
            content = json.dumps(value, ensure_ascii=False).encode() if mime.startswith('application/json') else value
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)

        def check_host(self):
            if self.headers.get('Host') not in allowed_hosts:
                raise Problem('访问地址无效，请使用部署时的地址。', 403)

        def do_GET(self):
            try:
                self.check_host()
                path = urlsplit(self.path).path
                if path == '/api/bootstrap':
                    self.reply(200, setup.accounts.snapshot() | {'environment': setup.environment(),
                        'device': '本机' if bind in ('127.0.0.1', 'localhost') else '当前服务器', 'login': setup.login_status()})
                elif path == '/api/login':
                    self.reply(200, setup.login_status())
                elif path in ASSETS:
                    name, mime = ASSETS[path]
                    self.reply(200, (PUBLIC / name).read_bytes(), mime)
                else:
                    self.reply(404, {'error': '页面不存在。'})
            except Problem as error:
                self.reply(error.status, {'error': str(error)})
            except (OSError, sqlite3.Error):
                self.reply(503, {'error': '环境与账号信息暂时无法读取，请稍后重试。'})

        def mutate(self):
            try:
                self.check_host()
                origin = self.headers.get('Origin')
                if (origin and urlsplit(origin).netloc != self.headers.get('Host')) or self.headers.get('Sec-Fetch-Site') == 'cross-site' or self.headers.get('X-Okeeper-Request') != '1':
                    raise Problem('请求来源无效。', 403)
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise Problem('请使用 JSON 请求。', 415)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 2048:
                    raise Problem('请求内容过长。', 413)
                data = json.loads(self.rfile.read(length) or '{}')
                if not isinstance(data, dict):
                    raise Problem('请求内容无效。')
                path = urlsplit(self.path).path
                if self.command == 'POST' and path == '/api/accounts/bind' and set(data) == {'candidate_id'}:
                    self.reply(200, setup.bind(data['candidate_id']))
                elif self.command == 'POST' and path == '/api/accounts/select' and set(data) == {'account_id'}:
                    self.reply(200, setup.accounts.select(data['account_id']))
                elif self.command == 'POST' and path == '/api/login' and not data:
                    self.reply(202, setup.start_login())
                elif self.command == 'DELETE' and path == '/api/login' and not data:
                    self.reply(200, setup.cancel_login())
                elif self.command == 'DELETE' and re.fullmatch(r'/api/accounts/[a-f0-9]{24}', path) and not data:
                    self.reply(200, setup.accounts.delete(path.rsplit('/', 1)[1]))
                else:
                    raise Problem('请求内容或操作无效。')
            except Problem as error:
                self.reply(error.status, {'error': str(error)})
            except (ValueError, TypeError):
                self.reply(400, {'error': '请求内容无效。'})
            except (OSError, sqlite3.Error):
                self.reply(503, {'error': '账号记录暂时无法保存，请稍后重试。'})

        do_POST = mutate
        do_DELETE = mutate

    return Handler


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18794)
    parser.add_argument('--state-dir', default=os.environ.get('OKEEPER_SETUP_STATE_DIR', str(Path.home() / '.local/state/okeeper-open-source')))
    args = parser.parse_args()
    if args.bind not in ('127.0.0.1', 'localhost'):
        try:
            if ipaddress.ip_address(args.bind) not in ipaddress.ip_network('100.64.0.0/10'):
                raise ValueError()
        except ValueError:
            parser.error('此单用户版本仅支持 localhost 或 Tailscale IPv4。')
    setup = Setup(Accounts(args.state_dir))
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(setup, args.bind, args.port))
    server.daemon_threads = True
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    print(f'Okeeper setup listening on http://{args.bind}:{args.port}', flush=True)
    try:
        server.serve_forever()
    finally:
        setup.cancel_login()
        server.server_close()


if __name__ == '__main__':
    main()
