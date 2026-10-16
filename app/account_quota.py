"""Multi-account quota, using CC Switch's WHAM usage and OAuth protocols.

Reference: farion1231/cc-switch, services/subscription.rs::query_codex_usage.
Credentials stay local. Live Codex login is never switched or overwritten.
"""
import base64
import copy
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

from rpc import RPC

from settings import STATE,CODEX_HOME
AUTH = CODEX_HOME / 'auth.json'
CC_STORE = Path.home() / '.cc-switch' / 'codex_oauth_auth.json'
CC_DATABASE = Path.home() / '.cc-switch' / 'cc-switch.db'
TOKEN_STATE = STATE / 'quota-access.json'
USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage'


def claims(token):
    try:
        part = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (ValueError, IndexError, TypeError):
        return {}


def identity(auth):
    tokens = auth.get('tokens') or {}
    user = claims(tokens.get('id_token') or '')
    account_id = tokens.get('account_id')
    if not account_id:
        raise ValueError('当前未登录 Codex 订阅账号')
    email = user.get('email') or ''
    label = email or '当前登录账号'
    return (account_id, user.get('sub'), email), label


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize(raw, label):
    windows = []
    limits = raw.get('rate_limit') or {}
    for key in ('primary_window', 'secondary_window'):
        window = limits.get(key)
        if not isinstance(window, dict) or not number(window.get('used_percent')):
            continue
        used = min(100, max(0, window['used_percent']))
        seconds = window.get('limit_window_seconds')
        reset = window.get('reset_at')
        windows.append({
            'id': key, 'used_percent': used, 'remaining_percent': round(100 - used, 2),
            'window_seconds': seconds if number(seconds) and seconds > 0 else None,
            'resets_at': reset if number(reset) and 0 < reset < 253402300800 else None,
        })
    if not windows:
        raise ValueError('此账号暂未提供额度数据')
    plan = raw.get('plan_type')
    return {'account': label, 'plan': plan if isinstance(plan, str) else None,
            'windows': windows, 'updated_at': datetime.now(timezone.utc).isoformat(),
            'stale': False, 'error': None}


def fetch_usage(access, account_id):
    request = Request(USAGE_URL, headers={
        'Authorization': 'Bearer ' + access, 'ChatGPT-Account-Id': account_id,
        'User-Agent': 'codex-cli', 'Accept': 'application/json',
    })
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read(1048576))


def read_usage(auth, account_key):
    """Same GET/headers as CC Switch; delegate expired credentials to Codex."""
    tokens = auth.get('tokens') or {}
    try:
        access = tokens.get('access_token')
        if not access:
            raise ValueError('No live access token')
        return fetch_usage(access, account_key[0])
    except Exception:
        # The running app-server already manages refreshed credentials. It must
        # report the same account, never silently substitute another login.
        client = RPC()
        try:
            result = client.call('account/rateLimits/read', {})
            if result.get('accountId') != account_key[0]:
                raise ValueError('Account changed')
            rate = result.get('rateLimits') or {}
            limits = {}
            for source, dest in (('primary', 'primary_window'), ('secondary', 'secondary_window')):
                w = rate.get(source)
                if isinstance(w, dict):
                    mins = w.get('windowDurationMins')
                    limits[dest] = {'used_percent': w.get('usedPercent'),
                                    'limit_window_seconds': mins * 60 if number(mins) else None,
                                    'reset_at': w.get('resetsAt')}
            return {'plan_type': rate.get('planType'), 'rate_limit': limits}
        finally:
            client.close()


class QuotaCache:
    """Single flight and bounded polling, with stale data scoped to one account."""
    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.value = None
        self.attempted = None

    def get(self, force=False):
        with self.lock:
            try:
                auth = json.loads(AUTH.read_text())
                key, label = identity(auth)
            except Exception:
                self.key = self.value = self.attempted = None
                return {'account': '当前账号', 'plan': None, 'windows': [], 'updated_at': None,
                        'stale': False, 'error': '请先在 Codex 登录订阅账号'}
            if key != self.key:
                self.key, self.value, self.attempted = key, None, None
            now = time.monotonic()
            ttl = 10 if force else (30 if self.value and self.value.get('error') else 60)
            if self.value is not None and self.attempted is not None and now - self.attempted < ttl:
                return copy.deepcopy(self.value)
            try:
                value = normalize(read_usage(auth, key), label)
                # A login switch during the request must not publish the old account.
                if identity(json.loads(AUTH.read_text()))[0] != key:
                    raise ValueError('Account changed')
                self.value = value
            except Exception:
                try:
                    same_account = identity(json.loads(AUTH.read_text()))[0] == key
                except Exception:
                    same_account = False
                if not same_account:
                    self.value = None
                value = copy.deepcopy(self.value) if self.value else {
                    'account': label, 'plan': None, 'windows': [], 'updated_at': None}
                value.update(stale=bool(value['windows']), error='暂时无法更新额度，请稍后重试')
                self.value = value
            self.attempted = time.monotonic()
            return copy.deepcopy(self.value)


class LoginRequired(Exception):
    pass


def public_id(key):
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]


def discover_accounts():
    """Known live/CC Switch locations only; no history or backup credential scan."""
    records = {}
    warning = None

    def add(auth, source, managed_id=None, current=False):
        try:
            key, label = identity(auth)
        except Exception:
            return
        previous = records.get(key)
        record = {'key': key, 'label': label, 'auth': auth, 'source': source,
                  'managed_id': managed_id, 'current': current, 'id': public_id(key)}
        if previous:
            # Live credentials take priority; CC Switch's auth center owns its
            # saved accounts. A provider snapshot may supply a still-valid token.
            if current or previous['source'] == 'provider':
                record['alternate_auth'] = previous['auth']
                records[key] = record
            else:
                previous['alternate_auth'] = auth
        else:
            records[key] = record

    try:
        with closing(sqlite3.connect(f'file:{CC_DATABASE}?mode=ro', uri=True, timeout=2)) as db:
            for (config,) in db.execute("SELECT settings_config FROM providers WHERE app_type='codex'"):
                try: add(json.loads(config).get('auth') or {}, 'provider')
                except (ValueError, AttributeError): pass
    except (OSError, sqlite3.Error):
        if CC_DATABASE.exists(): warning = '部分账号来源暂时无法读取'
    try:
        store = json.loads(CC_STORE.read_text())
        for managed_id, account in store.get('accounts', {}).items():
            add({'tokens': {'account_id': account.get('chatgpt_account_id'),
                            'id_token': account.get('id_token'),
                            'refresh_token': account.get('refresh_token')}}, 'cc-switch', managed_id)
    except FileNotFoundError:
        pass
    except Exception:
        warning = '部分账号来源暂时无法读取'
    # Native profiles are also valid account sources. Never expose their paths.
    from auth_profiles import profiles
    for profile in profiles(AUTH.parent).values():
        add(profile['auth'],'profile')
    try: add(json.loads(AUTH.read_text()), 'live', current=True)
    except Exception: pass
    return sorted(records.values(), key=lambda r: (not r['current'], r['key'][2] or '', r['id'])), warning


def cc_switch_running():
    # CC Switch does not reload its OAuth store while running. Do not race its
    # in-memory refresh-token generation; an existing valid access token is fine.
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal():
            try:
                if (entry / 'comm').read_text().strip() == 'cc-switch': return True
            except OSError: pass
    return False


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='rhythm-quota-', dir=os.environ.get('TMPDIR', str(Path.home() / 'tmp')))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


class SavedTokens:
    """Refresh saved accounts without changing Codex's active auth.json."""
    def access(self, record, force=False):
        TOKEN_STATE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_STATE.with_suffix('.lock'), 'a') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._access(record,force)

    def _access(self, record, force=False):
        try: cached = json.loads(TOKEN_STATE.read_text())
        except (OSError, ValueError): cached = {}
        auth = record['auth']
        store = None
        managed_id = record.get('managed_id')
        if managed_id:
            store = json.loads(CC_STORE.read_text())
            account = store['accounts'].get(managed_id)
            if account is None: raise LoginRequired('账号已从 CC Switch 移除')
            auth = {'tokens': {'account_id': account.get('chatgpt_account_id'), 'id_token': account.get('id_token'),
                               'refresh_token': account.get('refresh_token')}}
            if identity(auth)[0] != record['key']: raise LoginRequired('账号已变更，请刷新')
        refresh = (auth.get('tokens') or {}).get('refresh_token') or ''
        generation = hashlib.sha256(refresh.encode()).hexdigest()
        saved = cached.get(record['id'], {})
        if not force and saved.get('generation') == generation and saved.get('expires', 0) > time.time() + 90:
            return saved['access_token']
        for candidate in (auth, record.get('alternate_auth') or {}):
            token = (candidate.get('tokens') or {}).get('access_token')
            if not force and token and claims(token).get('exp', 0) > time.time() + 90:
                return token
        if not managed_id or not refresh:
            raise LoginRequired('请在 CC Switch 重新登录此账号')
        if cc_switch_running():
            raise LoginRequired('请退出 CC Switch 后刷新额度')
        request = Request('https://auth.openai.com/oauth/token', data=urlencode({
            'grant_type': 'refresh_token', 'refresh_token': refresh,
            'client_id': 'app_EMoamEEZ73f0CkXaXp7hrann', 'scope': 'openid profile email',
        }).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'codex-cli'})
        try:
            with urlopen(request, timeout=20) as response:
                tokens = json.loads(response.read(1048576))
        except HTTPError as error:
            if error.code in (400, 401, 403): raise LoginRequired('请在 CC Switch 重新登录此账号') from None
            raise
        access = tokens.get('access_token')
        if not isinstance(access, str) or not access: raise ValueError('Missing token')
        new_id = tokens.get('id_token') or auth['tokens'].get('id_token')
        verify = {'tokens': {'account_id': record['key'][0], 'id_token': new_id}}
        if identity(verify)[0] != record['key']: raise ValueError('Refreshed identity mismatch')
        workspace = claims(new_id).get('https://api.openai.com/auth', {}).get('chatgpt_account_id')
        if workspace and workspace != record['key'][0]: raise ValueError('Refreshed workspace mismatch')
        # Re-read immediately before committing: preserve other accounts/default
        # settings, and never overwrite a newer refresh or resurrect a deletion.
        latest = json.loads(CC_STORE.read_text())
        current = latest.get('accounts', {}).get(managed_id)
        if current is None or current.get('refresh_token') != refresh:
            raise ValueError('Saved login changed during refresh')
        current['refresh_token'] = tokens.get('refresh_token') or refresh
        current['id_token'] = new_id
        current['token_updated_at_ms'] = int(time.time() * 1000)
        atomic_json(CC_STORE, latest)
        generation = hashlib.sha256(current['refresh_token'].encode()).hexdigest()
        expires = claims(access).get('exp')
        if not number(expires): expires = time.time() + float(tokens.get('expires_in', 3600))
        cached[record['id']] = {'access_token': access, 'expires': expires, 'generation': generation}
        atomic_json(TOKEN_STATE, cached)
        return access


class AllQuotas:
    def __init__(self):
        self.lock = threading.Lock()
        self.live = QuotaCache()
        self.saved_tokens = SavedTokens()
        self.values = {}

    def read(self, record, force):
        if record['current']:
            value = self.live.get(force)
            try: unchanged = identity(json.loads(AUTH.read_text()))[0] == record['key']
            except Exception: unchanged = False
            if not unchanged:
                value = {'account': record['label'], 'plan': None, 'windows': [], 'updated_at': None,
                         'stale': False, 'error': '登录账号已变更，请刷新'}
        else:
            old = self.values.get(record['id'])
            ttl = 10 if force else (300 if old and old[1].get('login_required') else 60)
            if old and time.monotonic() - old[0] < ttl:
                value = copy.deepcopy(old[1])
            else:
                try:
                    access = self.saved_tokens.access(record)
                    try: raw=fetch_usage(access, record['key'][0])
                    except HTTPError as error:
                        if error.code!=401:raise
                        access=self.saved_tokens.access(record,force=True)
                        raw=fetch_usage(access,record['key'][0])
                    value = normalize(raw, record['label'])
                except Exception as error:
                    value = copy.deepcopy(old[1]) if old else {'account': record['label'], 'plan': None,
                                                             'windows': [], 'updated_at': None}
                    value.update(stale=bool(value['windows']), login_required=isinstance(error, LoginRequired),
                                 error=str(error) if isinstance(error, LoginRequired) else '暂时无法更新额度')
                self.values[record['id']] = (time.monotonic(), copy.deepcopy(value))
        value.update(id=record['id'], current=record['current'])
        return value

    def get(self, force=False):
        with self.lock:
            records, warning = discover_accounts()
            ids = {r['id'] for r in records}
            self.values = {k: v for k, v in self.values.items() if k in ids}
            # One slow/offline account must not serialize queries of the others.
            with ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(lambda record: self.read(record, force), records))
            # Drop accounts removed or switched while the requests were in flight.
            current, latest_warning = discover_accounts()
            valid = {r['id']: r for r in current}
            values = [v for v in values if v['id'] in valid]
            for value in values: value['current'] = valid[value['id']]['current']
            return {'accounts': values, 'error': warning or latest_warning or (None if values else '暂未找到 Codex 账号')}


cache = AllQuotas()
