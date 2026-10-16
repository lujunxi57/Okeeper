"""Controlled manual/automatic switch/restart, independently of the main daemon.

Native profile switching: stop -> fresh backup -> native profile -> start.
Never accepts a path, shell command, token, or model from the browser.
"""
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
from settings import worker_environment
import sys
import time
import uuid

from account_quota import atomic_json,identity
from auth_profiles import HOME,canonical,profiles,read_auth,refreshed_native
from rpc import BIN,RPC

from settings import STATE
ACTIVE={'queued','stopping','backing_up','activating','starting','verifying','rolling_back'}


class DaemonCommandError(RuntimeError):
    def __init__(self,action,code,category):
        self.action=action;self.code=code;self.category=category
        super().__init__(f'daemon {action}: {category} (exit {code})')


def classify_daemon_error(message):
    text=message.lower()
    if 'not managed by codex app-server daemon' in text:return 'unmanaged_runtime'
    if 'daemon executable not found' in text:return 'missing_package'
    if 'permission denied' in text or 'operation not permitted' in text:return 'permission_denied'
    if 'timed out' in text:return 'daemon_timeout'
    return 'daemon_command_failed'


def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()


def load(state=None):
    try:return json.loads((Path(state or STATE)/'account-control.json').read_text())
    except (OSError,ValueError):return None


def save(value,state=None):atomic_json(Path(state or STATE)/'account-control.json',value)


@contextmanager
def locked(state=None):
    state=Path(state or STATE);state.mkdir(parents=True,exist_ok=True)
    with (state/'account-control.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def recent_busy(value):
    if not value or value.get('status') not in ACTIVE:return False
    try:return time.time()-dt.datetime.fromisoformat(value['updated_at']).timestamp()<420
    except (KeyError,ValueError,TypeError):return False


def inspect_runtime(home=None,include_tasks=False):
    home=Path(home or HOME)
    client=RPC(socket=str(home/'app-server-control/app-server-control.sock'))
    try:
        value=client.call('account/read',{'refreshToken':False})
        account=value.get('account') or {};routing=value.get('workspaceRouting') or {}
        result={'running':True,'email':account.get('email'),'workspace':routing.get('chatgptAccountId'),'active_tasks':None}
        if include_tasks:
            try:
                ids=client.call('thread/loaded/list',{})['data'];active=0;deadline=time.monotonic()+3
                for thread in ids[:40]:
                    if time.monotonic()>deadline:break
                    info=client.call('thread/read',{'threadId':thread})['thread']
                    if info.get('status',{}).get('type')=='active':active+=1
                result['active_tasks']=active
            except Exception:pass
        return result
    finally:client.close()


def public_state(state=None,home=None):
    state=Path(state or STATE);home=Path(home or HOME);value=load(state)
    # A worker killed or a launch lost must not leave a permanent "working" UI.
    if value and value.get('status') in ACTIVE and not recent_busy(value):
        value={**value,'status':'failed','message':'操作未确认完成，请检查后台状态后重试'}
    try:
        if recent_busy(value):raise ConnectionError('Busy')
        runtime=inspect_runtime(home,include_tasks=True)
    except Exception:runtime={'running':False,'email':None,'workspace':None,'active_tasks':None}
    saved=profiles(home)
    return {'operation':value,'busy':recent_busy(value),'runtime':runtime,
            'accounts':[{'id':a['id'],'name':a['name'],'switchable':True} for a in saved.values()]}


def launch(job):
    subprocess.run(['systemd-run','--user','--quiet','--collect','--unit=okeeper-control-'+job,
                    '--property=Type=exec','--property=Restart=no','--property=KillMode=process',
                    '--property=RuntimeMaxSec=360','--property=UMask=0077',*worker_environment(),sys.executable,
                    str(Path(__file__).resolve()),'run',job],check=True,timeout=10,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def request_operation(payload,state=None,home=None,launcher=None,*,automatic=None):
    state=Path(state or STATE);home=Path(home or HOME)
    allowed={'action','account_id','confirmed','request_id'}
    if not isinstance(payload,dict) or set(payload)-allowed or payload.get('confirmed') is not True:
        raise ValueError('请先确认重启会中断主后台任务')
    action=payload.get('action');account_id=payload.get('account_id','');request_id=payload.get('request_id')
    if action not in ('switch','restart') or not isinstance(account_id,str) or (action=='restart' and account_id):
        raise ValueError('操作无效')
    try:request_id=str(uuid.UUID(request_id))
    except (ValueError,TypeError,AttributeError):raise ValueError('操作标识无效') from None
    if action=='switch' and account_id not in profiles(home):raise ValueError('此账号没有可用的原生登录副本，请先重新登录')
    old=load(state)
    if recent_busy(old):
        if old.get('request_id')==request_id:return old
        raise BlockingIOError('已有切换或重启正在进行')
    with locked(state):
        old=load(state)
        if old and old.get('request_id')==request_id:return old
        if recent_busy(old):raise BlockingIOError('已有切换或重启正在进行')
        # Preserve request IDs durably: old browser retries cannot restart again.
        receipts=state/'account-control-receipts';receipts.mkdir(exist_ok=True,mode=0o700)
        receipt=receipts/(request_id+'.json')
        if receipt.exists():return json.loads(receipt.read_text())
        record={'id':uuid.uuid4().hex,'request_id':request_id,'action':action,'account_id':account_id,
                'account':profiles(home)[account_id]['name'] if action=='switch' else None,
                'status':'queued','message':'正在准备重启','created_at':stamp(),'updated_at':stamp()}
        if automatic is not None:record['automatic']=automatic
        save(record,state);atomic_json(receipt,record)
        try:(launcher or launch)(record['id'])
        except Exception:
            record.update(status='failed',message='无法启动操作进程，账号未切换',updated_at=stamp())
            save(record,state);atomic_json(receipt,record)
        return record


def daemon(action,home=None):
    if action not in ('start','stop','restart'):raise ValueError('Invalid daemon command')
    env={k:v for k,v in os.environ.items() if k not in ('CODEX_HOME','CODEX_SQLITE_HOME','CODEX_ACCESS_TOKEN','CODEX_API_KEY','OPENAI_API_KEY')}
    env['CODEX_HOME']=str(home or HOME)
    try:
        result=subprocess.run([str(Path(BIN).resolve()),'app-server','daemon',action],env=env,
                              check=False,timeout=35,capture_output=True,text=True)
    except subprocess.TimeoutExpired:raise DaemonCommandError(action,None,'daemon_timeout') from None
    if result.returncode:
        # Store only an allowlisted diagnosis, never raw CLI output or tokens.
        raise DaemonCommandError(action,result.returncode,classify_daemon_error(result.stderr))


def verify(home,expected):
    # Credential-aware health, not just PID/version or a successful start command.
    deadline=time.monotonic()+18
    while True:
        try:
            runtime=inspect_runtime(home)
            if runtime['running'] and runtime['workspace']==expected[0] and (runtime['email'] or '').casefold()==(expected[2] or '').casefold():return runtime
        except Exception:pass
        if time.monotonic()>=deadline:raise RuntimeError('Backend account not verified')
        time.sleep(.4)


def perform(job,state=None,home=None,runner=None,verifier=None):
    state=Path(state or STATE);home=Path(home or HOME);runner=runner or daemon;verifier=verifier or verify
    with locked(state):
        record=load(state)
        if not record or record['id']!=job or record['status']!='queued':return
        def progress(status,message):
            record.update(status=status,message=message,updated_at=stamp());save(record,state)
            atomic_json(state/'account-control-receipts'/(record['request_id']+'.json'),record)
        original=None;stopped=False;activated=False
        try:
            if record.get('automatic'):
                from quota_watch import authorized
                try:proceed=authorized(record,state,home)
                except Exception:proceed=False
                if not proceed:
                    progress('cancelled','自动切换条件已变化，本次未重启')
                    return
            target=None
            if record['action']=='switch':
                account=profiles(home).get(record['account_id'])
                if not account:raise ValueError('Missing native profile')
                target=read_auth(account['path'])
                if target['id']!=record['account_id']:raise ValueError('Native profile changed')
            progress('stopping','正在停止主后台')
            runner('stop',home);stopped=True
            # Capture only AFTER stop: includes the daemon's latest token refresh.
            progress('backing_up','正在保存当前账号')
            original=read_auth(home/'auth.json')
            directory=home/'auth-profiles';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            backup=directory/('before-switch-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+job[:8]+'.json')
            atomic_json(backup,original['auth']);atomic_json(canonical(home,original['auth']),original['auth'])
            if target:
                # Switching to the current identity must keep its freshly saved
                # generation, never roll it back to a stale target snapshot.
                if target['key']==original['key']:target=original
                else:target=refreshed_native(target)
                progress('activating','正在激活目标账号')
                atomic_json(home/'auth.json',target['auth']);activated=True
            expected=(target or original)['key']
            progress('starting','正在启动主后台')
            runner('start',home)
            progress('verifying','正在核对后台登录账号')
            runtime=verifier(home,expected)
            # Save refreshed native credentials as the long-lived target profile.
            current=read_auth(home/'auth.json')
            if current['key']!=expected:raise ValueError('Current auth differs after startup')
            atomic_json(canonical(home,current['auth']),current['auth'])
            record['account']=runtime['email']
            progress('completed','账号已切换并重启，请重新连接 SSH' if target else '后台已重启，请重新连接 SSH')
        except Exception as error:
            if isinstance(error,DaemonCommandError):
                record['diagnostic']={'step':error.action,'code':error.category,'exit_code':error.code}
            if stopped:
                progress('rolling_back','操作未完成，正在恢复原账号')
                try:
                    if activated:
                        runner('stop',home)
                        atomic_json(home/'auth.json',original['auth'])
                    runner('start',home)
                    if original:verifier(home,original['key'])
                    progress('failed','操作未完成，已恢复原账号；目标凭据可能需要重新登录')
                except Exception:progress('failed','操作未完成，恢复也未确认成功，请通过 SSH 检查后台')
            else:
                messages={'unmanaged_runtime':'主后台安装入口与进程登记不一致，登录文件未修改',
                          'missing_package':'Codex 后台安装入口不可用，登录文件未修改',
                          'permission_denied':'停止主后台的权限不足，登录文件未修改',
                          'daemon_timeout':'停止主后台超时，登录文件未修改'}
                progress('failed',messages.get(getattr(error,'category',None),'未能停止主后台，登录文件未修改'))


if __name__=='__main__' and len(sys.argv)==3 and sys.argv[1]=='run':perform(sys.argv[2])
