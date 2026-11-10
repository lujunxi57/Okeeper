"""Device-code authorization in a disposable Home; never logs out the main app."""
import atexit
from contextlib import ExitStack
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from account_preferences import disabled_ids
from account_quota import atomic_json,discover_accounts
from auth_profiles import canonical,read_auth
from codex_stdio import NativeClient
from settings import STATE,CODEX_HOME


def accounts(state=None):
    disabled=disabled_ids(state)
    records,_=discover_accounts(include_disabled=True)
    return [{'id':r['id'],'name':r['label'],'current':r['current'],'enabled':r['id'] not in disabled} for r in records]


class LoginManager:
    def __init__(self,state=STATE,home=CODEX_HOME):
        self.state=Path(state);self.home=Path(home);self.lock=threading.RLock()
        self.value={'status':'idle'};self.cancelled=None;self.client=None;self.worker=None
    def status(self):
        with self.lock:return dict(self.value)
    def start(self):
        with self.lock:
            if self.value['status'] in ('starting','waiting'):return dict(self.value)
            self.state.mkdir(parents=True,exist_ok=True)
            self.value={'id':uuid.uuid4().hex,'status':'starting','expires_at':int(time.time())+600}
            cancel=self.cancelled=threading.Event()
            self.worker=threading.Thread(target=self._run,args=(self.value['id'],cancel),daemon=True)
            self.worker.start();return dict(self.value)
    def publish(self,id,**fields):
        with self.lock:
            if self.value.get('id')==id and self.value['status']!='cancelled':self.value.update(fields)
    def _run(self,id,cancel):
        client=None
        try:
            with ExitStack() as stack:
                temporary=stack.enter_context(tempfile.TemporaryDirectory(prefix='account-login-',dir=self.state))
                client=NativeClient(temporary)
                stack.callback(client.close)
                with self.lock:
                    if cancel.is_set():return
                    self.client=client
                result=client.call('account/login/start',{'type':'chatgptDeviceCode'})
                url=result.get('verificationUrl','');parsed=urlsplit(url)
                if (parsed.scheme!='https' or parsed.hostname!='auth.openai.com' or parsed.username or parsed.password or
                    parsed.port not in (None,443) or not isinstance(result.get('userCode'),str) or not result['userCode']):
                    raise ValueError('Invalid login response')
                self.publish(id,status='waiting',url=url,code=result['userCode'])
                deadline=time.monotonic()+600
                while not cancel.is_set() and time.monotonic()<deadline:
                    try:message=client.events.pop(0) if client.events else client.receive(.5)
                    except queue.Empty:continue
                    if message.get('method')!='account/login/completed':continue
                    params=message.get('params') or {}
                    if result.get('loginId') and params.get('loginId')!=result['loginId']:continue
                    if not params.get('success'):raise ValueError('Authorization failed')
                    info=client.call('account/read',{'refreshToken':False})
                    profile=read_auth(Path(temporary)/'auth.json');account=info.get('account') or {}
                    workspace=(info.get('workspaceRouting') or {}).get('chatgptAccountId')
                    if (account.get('type')!='chatgpt' or not profile['key'][2] or
                        (account.get('email') or '').casefold()!=profile['key'][2].casefold() or
                        (workspace is not None and workspace!=profile['key'][0])):
                        raise ValueError('Identity mismatch')
                    # Serialize credential writes with the native refresh owner.
                    from account_quota import TOKEN_STATE
                    import fcntl
                    TOKEN_STATE.parent.mkdir(parents=True,exist_ok=True)
                    with TOKEN_STATE.with_suffix('.lock').open('a') as token_lock:
                        fcntl.flock(token_lock,fcntl.LOCK_EX)
                        with self.lock:
                            if cancel.is_set():return
                            atomic_json(canonical(self.home,profile['auth']),profile['auth'])
                            ownership=self.state/'account-authorizations.json'
                            try:owned=json.loads(ownership.read_text())
                            except FileNotFoundError:owned=[]
                            atomic_json(ownership,sorted(set(owned)|{profile['id']}))
                            self.publish(id,status='completed',account_id=profile['id'],name=profile['name'],code=None,url=None)
                    return
                if not cancel.is_set():self.publish(id,status='expired',code=None,url=None,error='授权已过期，请重新开始登录')
        except Exception:
            if not cancel.is_set():self.publish(id,status='failed',code=None,url=None,error='登录未完成，请确认网络及 ChatGPT 的设备码登录设置后重试')
        finally:
            with self.lock:
                if self.client is client:self.client=None
    def cancel(self):
        with self.lock:
            if self.cancelled:self.cancelled.set()
            client=self.client;worker=self.worker
            if self.value['status'] in ('starting','waiting'):self.value.update(status='cancelled',code=None,url=None)
        if client:client.close()
        if worker and worker is not threading.current_thread():worker.join(timeout=6)
        return self.status()


login=LoginManager()
atexit.register(login.cancel)
