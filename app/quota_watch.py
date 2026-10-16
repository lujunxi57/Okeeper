"""Local quota monitor. Decisions use fresh quotas and the running daemon identity."""
import datetime as dt
import fcntl
import json
from pathlib import Path
import time
import uuid

import account_control as control
from account_quota import atomic_json, cache, number
from auth_profiles import HOME, profiles, read_auth


def config(state):
    try:
        value=json.loads((Path(state)/'quota-watch-config.json').read_text())
        if type(value.get('enabled')) is bool and isinstance(value.get('generation'),str):return value
    except (OSError,ValueError,TypeError):pass
    return {'enabled':False,'generation':'0'}


def public_state(state):
    value=config(state)
    try:result=json.loads((Path(state)/'quota-watch-result.json').read_text())
    except (OSError,ValueError):result={}
    if result.get('generation')!=value['generation']:result={}
    return {**result,**value,'interval_seconds':60}


def configure(state,payload):
    if not isinstance(payload,dict) or set(payload)!={'enabled'} or type(payload['enabled']) is not bool:
        raise ValueError('自动切换设置无效')
    state=Path(state);state.mkdir(parents=True,exist_ok=True)
    with (state/'quota-watch-config.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        old=config(state)
        if old['enabled']!=payload['enabled']:
            atomic_json(state/'quota-watch-config.json',{'enabled':payload['enabled'],'generation':uuid.uuid4().hex})
    return public_state(state)


def fresh(account,now):
    if account.get('stale') or account.get('error') or account.get('login_required'):return False
    try:age=now-dt.datetime.fromisoformat(account['updated_at'].replace('Z','+00:00')).timestamp()
    except (KeyError,ValueError,TypeError):return False
    return -5<=age<=90


def five_hour(account):
    return next((w for w in account.get('windows',[]) if w.get('window_seconds')==18000),None)


def exhausted(account,now):
    window=five_hour(account)
    return bool(fresh(account,now) and window and number(window.get('used_percent')) and
                window['used_percent']>=100 and number(window.get('resets_at')) and window['resets_at']>now)


def usable(account,now):
    window=five_hour(account)
    return bool(fresh(account,now) and window and number(window.get('remaining_percent')) and
                window['remaining_percent']>0 and all(number(w.get('remaining_percent')) and
                w['remaining_percent']>0 for w in account.get('windows',[])))


def runtime_account(home,runtime):
    if not runtime.get('running'):return None
    saved=profiles(home)
    try:
        current=read_auth(Path(home)/'auth.json');saved[current['id']]=current
    except (OSError,ValueError):pass
    matches=[a['id'] for a in saved.values() if a['key'][0]==runtime.get('workspace') and
             a['name'].casefold()==(runtime.get('email') or '').casefold()]
    return matches[0] if len(matches)==1 else None


def request_id(generation,current,reset,target):
    return str(uuid.uuid5(uuid.NAMESPACE_URL,f'rhythm-quota:{generation}:{current}:{reset}:{target}'))


def authorized(record,state,home):
    """Recheck immediately before stopping, under the shared account-control lock."""
    guard=record['automatic'];setting=config(state)
    if not setting['enabled'] or setting['generation']!=guard['generation']:return False
    if runtime_account(home,control.inspect_runtime(home))!=guard['current']:return False
    values={a['id']:a for a in cache.get(force=True)['accounts']};now=time.time()
    current=values.get(guard['current'],{});target=values.get(record['account_id'],{})
    return bool(exhausted(current,now) and five_hour(current)['resets_at']==guard['reset'] and usable(target,now)
                and runtime_account(home,control.inspect_runtime(home))==guard['current']
                and config(state)==setting)


def tick(state=None,home=None,quota_reader=None,runtime_reader=None,launcher=None,performer=None,clock=None):
    state=Path(state or control.STATE);home=Path(home or HOME);state.mkdir(parents=True,exist_ok=True)
    # One checker at a time, including manual invocations. Missed checks never queue.
    with (state/'quota-watch-tick.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        setting=config(state)
        if not setting['enabled']:return
        def result(status,message,**extra):
            atomic_json(state/'quota-watch-result.json',{'generation':setting['generation'],
                        'checked_at':control.stamp(),'status':status,'message':message,**extra})
        try:
            operation=control.load(state)
            if control.recent_busy(operation):return result('busy','后台正在切换或重启，稍后检查')
            now=(clock or time.time)()
            if operation and operation.get('status')=='completed':
                age=now-dt.datetime.fromisoformat(operation['updated_at']).timestamp()
                if age<120:return result('cooldown','后台刚完成重启，稍后检查')
            runtime=(runtime_reader or control.inspect_runtime)(home)
            current_id=runtime_account(home,runtime)
            if not current_id:return result('unavailable','暂时无法确认主后台账号')
            values={a['id']:a for a in (quota_reader or (lambda:cache.get(force=True)))()['accounts']}
            current=values.get(current_id,{})
            if not fresh(current,now):return result('unavailable','额度暂时无法更新，保留当前账号')
            if not exhausted(current,now):return result('watching','监测中')
            reset=five_hour(current)['resets_at'];saved=profiles(home)
            candidates=sorted((a for a in values.values() if a['id']!=current_id and a['id'] in saved and usable(a,now)),
                              key=lambda a:(-five_hour(a)['remaining_percent'],a['id']))
            for candidate in candidates:
                rid=request_id(setting['generation'],current_id,reset,candidate['id'])
                if (state/'account-control-receipts'/(rid+'.json')).exists():continue
                # User may disable monitoring while quota requests are in flight.
                if config(state)!=setting:return
                guard={'generation':setting['generation'],'current':current_id,'reset':reset}
                operation=control.request_operation({'action':'switch','account_id':candidate['id'],
                    'confirmed':True,'request_id':rid},state,home,launcher or (lambda _:None),automatic=guard)
                result('switching','五小时额度已用尽，正在切换账号',operation_id=operation['id'])
                (performer or control.perform)(operation['id'],state,home)
                operation=control.load(state)
                return result(operation['status'],operation['message'],operation_id=operation['id'])
            result('no_candidate','五小时额度已用尽，暂无可切换账号')
        except BlockingIOError:result('busy','后台正在切换或重启，稍后检查')
        except Exception:result('unavailable','监测暂时失败，保留当前账号')


if __name__=='__main__':tick()
