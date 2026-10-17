"""Lightweight account alarm: verify owner, route, send exactly once, observe."""
import datetime as dt
import fcntl
import json
from pathlib import Path
import sys
import time

import account_store as store
from account_rpc import find_account,matches,open_account,IsolatedRPC
from rpc import RPC
from schedule_store import database,now
from trigger import create_chat,run_turn

from settings import STATE
MAX_LATE=15


class PinnedAccountChanged(Exception):
    pass


class ObservedTurn:
    """Persist acceptance before observing events, including later failures."""
    def __init__(self,rpc,slot):self.rpc=rpc;self.slot=slot
    def __getattr__(self,name):return getattr(self.rpc,name)
    def call(self,method,params):
        value=self.rpc.call(method,params)
        if method=='turn/start':store.update(STATE,self.slot,'accepted',turn_id=value['turn']['id'])
        return value


def failure_reason(rpc,thread):
    if rpc and thread:
        try:
            history=rpc.call('thread/read',{'threadId':thread,'includeTurns':True})['thread']
            turns=history.get('turns') or []
            error=(turns[-1].get('error') or {}) if turns else {}
            if error.get('codexErrorInfo')=='usageLimitExceeded':
                return '此账号当前额度已用尽，本次未完成；不会自动补发'
        except Exception:pass
    return '账号触发未确认完成，请查看执行记录；不会自动补发'


def open_for_day(record,day):
    with database(STATE) as db:
        pinned=db.execute('SELECT route FROM account_daily_chats WHERE account_id=? AND day=? ORDER BY created_at LIMIT 1',
                          (record['id'],day)).fetchone()
    if pinned is None:return open_account(STATE,record)
    if pinned['route']=='isolated':
        return IsolatedRPC(STATE/'account-homes'/record['id'],record),'isolated'
    rpc=RPC()
    try:
        if not matches(rpc,record):raise PinnedAccountChanged()
        return rpc,'main'
    except Exception:
        rpc.close();raise


def log(event,**fields):
    value={'at':now().isoformat(),'event':event,**fields}
    with (STATE/'events.jsonl').open('a') as stream:stream.write(json.dumps(value,ensure_ascii=False)+'\n')


def cleanup(record,rpc,route,day,current_thread):
    deadline=time.monotonic()+30
    with database(STATE) as db:
        rows=db.execute('SELECT day,thread_id FROM account_daily_chats WHERE account_id=? AND route=? AND day<? ORDER BY day',
                        (record['id'],route,day)).fetchall()
    for old in rows:
        if time.monotonic()>deadline:return
        thread=old['thread_id']
        if thread==current_thread:continue
        try:
            info=rpc.call('thread/read',{'threadId':thread})['thread']
            if info.get('status',{}).get('type') not in ('idle','notLoaded'):continue
            if 'archived_sessions' not in Path(info.get('path') or '').parts:
                rpc.call('thread/archive',{'threadId':thread})
            rpc.call('thread/delete',{'threadId':thread})
            with database(STATE) as db:
                db.execute('DELETE FROM account_daily_chats WHERE account_id=? AND route=? AND day=? AND thread_id=?',
                           (record['id'],route,old['day'],thread))
            log('account_old_chat_deleted',account=record['id'],route=route,thread=thread)
        except Exception:
            log('account_cleanup_deferred',account=record['id'],route=route,thread=thread)


def daily_chat(record,rpc,route,day):
    with database(STATE) as db:
        row=db.execute('SELECT thread_id,route FROM account_daily_chats WHERE account_id=? AND day=?',
                       (record['id'],day)).fetchone()
    if row is not None and row['route']!=route:raise PinnedAccountChanged()
    fresh=row is None
    if fresh:
        thread=create_chat(rpc,f"每日定时触发 {day} · {record['label']}")
        with database(STATE) as db:
            db.execute('INSERT INTO account_daily_chats VALUES (?,?,?,?,?)',
                       (record['id'],day,route,thread,now().isoformat()))
    else:thread=row['thread_id']
    info=rpc.call('thread/read',{'threadId':thread})['thread']
    if 'archived_sessions' in Path(info.get('path') or '').parts:
        info=rpc.call('thread/unarchive',{'threadId':thread})['thread']
    if info.get('status',{}).get('type') not in ('idle','notLoaded'):return thread,False
    if not fresh:
        result=rpc.call('thread/resume',{'threadId':thread,'excludeTurns':True})
        if result['thread']['status']['type']!='idle':return thread,False
    return thread,True


def process(slot):
    run=store.begin(STATE,slot)
    if not run:return
    scheduled=dt.datetime.fromisoformat(run['scheduled'])
    if not 0<=(now()-scheduled).total_seconds()<=MAX_LATE:
        store.finish(STATE,run,'skipped_missed');return
    record=find_account(run['account_id'])
    if not record:
        store.finish(STATE,run,'failed_or_uncertain',error='指定账号已不存在');return
    lock=(STATE/('account-'+record['id']+'.lock')).open('a')
    rpc=None;route=None;thread=None
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            store.finish(STATE,run,'skipped_busy');return
        rpc,route=open_for_day(record,scheduled.date().isoformat())
        store.update(STATE,slot,'checking',route=route)
        thread,ready=daily_chat(record,rpc,route,scheduled.date().isoformat())
        store.update(STATE,slot,'checking',thread_id=thread)
        if not ready:store.finish(STATE,run,'skipped_busy');return
        if not store.unchanged(STATE,run):store.finish(STATE,run,'skipped_changed');return
        # This day's runtime is pinned once its chat is created. Never migrate
        # or switch a main-server login, even before sending the first turn.
        if not matches(rpc,record):
            raise PinnedAccountChanged()
        if not 0<=(now()-scheduled).total_seconds()<=MAX_LATE:
            store.finish(STATE,run,'skipped_missed');return
        if not store.unchanged(STATE,run):store.finish(STATE,run,'skipped_changed');return
        store.update(STATE,slot,'sending',started_at=now().isoformat())
        proof=run_turn(ObservedTurn(rpc,slot),thread,slot,scheduled)
        store.finish(STATE,run,'completed',turn_id=proof['turn_id'],tokens=proof.get('tokens',14824),completed_at=now().isoformat())
        log('account_turn_verified',account=record['id'],route=route,thread=thread,turn_id=proof['turn_id'],tokens=proof.get('tokens',14824))
    except PinnedAccountChanged:
        store.finish(STATE,run,'skipped_account_changed',error='当天固定的后台已切换账号，本次跳过；不会切号、迁移会话或自动补发')
    except Exception as error:
        store.finish(STATE,run,'failed_or_uncertain',error=failure_reason(rpc,thread))
        log('account_turn_unconfirmed',account=record['id'],route=route,thread=thread,error_type=type(error).__name__)
    finally:
        if rpc:
            if thread:
                cleanup(record,rpc,route,scheduled.date().isoformat(),thread)
                # Clear older chats in the other runtime as well, after sending.
                other='isolated' if route=='main' else 'main'
                with database(STATE) as db:
                    old=db.execute('SELECT 1 FROM account_daily_chats WHERE account_id=? AND route=? AND day<? LIMIT 1',
                                   (record['id'],other,scheduled.date().isoformat())).fetchone()
                if old:
                    other_rpc=None
                    try:
                        other_rpc=RPC() if other=='main' else IsolatedRPC(STATE/'account-homes'/record['id'],record)
                        cleanup(record,other_rpc,other,scheduled.date().isoformat(),thread)
                    except Exception:log('account_cleanup_deferred',account=record['id'],route=other)
                    finally:
                        if other_rpc:other_rpc.close()
            rpc.close()
        lock.close()


if __name__=='__main__':process(sys.argv[1])
