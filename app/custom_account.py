"""Select a custom task's account on the main Home before submitting its turn."""
from contextlib import contextmanager
import datetime as dt
import fcntl
import os
from pathlib import Path
import time
import uuid

import account_control as control
from account_preferences import enabled,runtime_enabled
from account_rpc import matches
from auth_profiles import profiles
import custom_store as store
from rpc import RPC
from schedule_store import database,now
from settings import CODEX_HOME

MAX_SWITCH_LATE=180


class NotSent(Exception):
    def __init__(self,status,message):self.status=status;super().__init__(message)


def selected_profile(run,state,home=CODEX_HOME):
    account_id=run['alarm'].get('account_id','')
    if not account_id:return None
    if not enabled(account_id,state):raise NotSent('skipped_disabled','指定账号已停用，本次未发送')
    profile=profiles(home).get(account_id)
    if not profile:raise NotSent('skipped_account_unavailable','指定账号登录不可用，请在账号管理重新授权')
    return profile


def verify_target(rpc,run,state,home=CODEX_HOME):
    profile=selected_profile(run,state,home)
    if profile and not matches(rpc,profile):raise NotSent('skipped_account_changed','主后台账号已变化，本次未发送')
    if not profile and not runtime_enabled(rpc,state):raise NotSent('skipped_disabled','主后台账号已停用，本次未发送')


def backend_idle(rpc,state,slot):
    """Fail closed on unreadable or incomplete state; include waiting approvals."""
    try:
        deadline=time.monotonic()+10
        page=rpc.call('thread/loaded/list',{})
        if page.get('nextCursor'):return False
        for id in page['data']:
            if time.monotonic()>deadline:return False
            thread=rpc.call('thread/read',{'threadId':id})['thread']
            if thread.get('status',{}).get('type') not in ('idle','notLoaded'):return False
        # turn/start can be accepted slightly before the runtime status catches
        # up. Okeeper's durable receipts cover that gap across its workers.
        cutoff=(now()-dt.timedelta(seconds=315)).isoformat()
        with database(state) as db:
            if db.execute("SELECT 1 FROM runs WHERE status IN ('claimed','sending') AND at>? LIMIT 1",(cutoff,)).fetchone():return False
            if db.execute("SELECT 1 FROM account_runs WHERE route='main' AND status IN ('checking','sending','accepted') AND at>? LIMIT 1",(cutoff,)).fetchone():return False
            if db.execute("SELECT 1 FROM custom_runs WHERE slot!=? AND status IN ('sending','accepted','running','waiting_input','observing_disconnected') LIMIT 1",(slot,)).fetchone():return False
        return True
    except Exception:return False


@contextmanager
def connection(run,state,rpc_factory=RPC,home=CODEX_HOME):
    """Exclude account changes during submission, release before observing.

    A specified account is an explicit authorization to switch at execution
    time. It never authorizes interrupting other work or resending a turn.
    """
    state=Path(state);lock=(state/'account-control.lock').open('a');rpc=None;entered=False
    try:
        os.chmod(lock.name,0o600)
        # Ordinary sends can share the current account concurrently. Only a
        # switch requires exclusive ownership through verification/submission.
        try:fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:raise NotSent('skipped_busy','后台正在处理账号操作，本次未发送') from None
        if control.recent_busy(control.load(state)):raise NotSent('skipped_busy','后台正在切换或重启，本次未发送')
        rpc=rpc_factory();profile=selected_profile(run,state,home)
        if profile and not matches(rpc,profile):
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise NotSent('skipped_busy','后台正在处理其他投递，本次未切换账号') from None
            # Check target availability before considering any backend restart.
            if not run['alarm'].get('new_session'):
                info=rpc.call('thread/read',{'threadId':run['thread_id']})['thread']
                if info.get('status',{}).get('type') not in ('idle','notLoaded'):
                    raise NotSent('skipped_busy','目标会话忙碌，本次未切换账号或发送')
            if not backend_idle(rpc,state,run['slot']):
                raise NotSent('skipped_busy','主后台仍有任务运行或状态未确认，本次未切换账号')
            if not store.unchanged(state,run):raise NotSent('skipped_changed','任务设置已变化，本次未发送')
            store.update(state,run['slot'],'switching',account_name=profile['name'],route='main')
            rpc.close();rpc=None
            def guard():
                if (now()-dt.datetime.fromisoformat(run['scheduled'])).total_seconds()>MAX_SWITCH_LATE:return False
                if not store.unchanged(state,run) or not enabled(profile['id'],state):return False
                check=None
                try:
                    check=rpc_factory()
                    return backend_idle(check,state,run['slot'])
                except Exception:return False
                finally:
                    if check:check.close()
            rid=str(uuid.uuid5(uuid.NAMESPACE_URL,'okeeper-custom-account:'+run['slot']))
            try:
                operation=control.request_operation({'action':'switch','account_id':profile['id'],'confirmed':True,'request_id':rid},
                    state,home,launcher=lambda _:None,already_locked=True)
                control.perform(operation['id'],state,home,already_locked=True,before_stop=guard)
            except Exception:
                raise NotSent('skipped_switch_failed','指定账号切换未完成，本次未发送；请查看后台操作记录') from None
            operation=control.load(state)
            if not operation or operation.get('request_id')!=rid or operation.get('status')!='completed':
                raise NotSent('skipped_switch_failed','指定账号切换未完成，本次未发送；请查看后台操作记录')
            rpc=rpc_factory()
            run['_switched_account']=True
        if not store.unchanged(state,run):raise NotSent('skipped_changed','任务设置已变化，本次未发送')
        verify_target(rpc,run,state,home)
        entered=True
        yield rpc
    finally:
        if rpc and not entered:rpc.close()
        lock.close()
