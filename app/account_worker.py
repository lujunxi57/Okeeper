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
from turn_result import FAILURE_MESSAGES, SendMissed, TurnError, read_turn

from settings import STATE
MAX_LATE=15


class AccountChanged(Exception):
    pass


class ObservedTurn:
    """Persist acceptance before observing events, including later failures."""
    def __init__(self,rpc,slot):
        self.rpc=rpc;self.slot=slot;self.turn_id=None;self.submit_started=False
    def __getattr__(self,name):return getattr(self.rpc,name)
    def call(self,method,params):
        if method=='turn/start':self.submit_started=True
        value=self.rpc.call(method,params)
        if method=='turn/start':
            self.turn_id=value['turn']['id']
            store.update(STATE,self.slot,'accepted',turn_id=self.turn_id)
        return value


def failure_reason(rpc,thread,observed,error):
    turn_id=observed.turn_id if observed else None
    if rpc and thread and turn_id:
        try:
            read_turn(rpc,thread,turn_id)
        except TurnError as terminal:
            return str(terminal)
        except Exception:pass
    if isinstance(error,TurnError):return str(error)
    if not observed or not observed.submit_started:return FAILURE_MESSAGES['preparation_failed']
    if not turn_id:return FAILURE_MESSAGES['submission_uncertain']
    return '账号触发未确认完成，请查看执行记录；不会自动补发'


def log(event,**fields):
    value={'at':now().isoformat(),'event':event,**fields}
    with (STATE/'events.jsonl').open('a') as stream:stream.write(json.dumps(value,ensure_ascii=False)+'\n')


def old_chats(db,record,route,day):
    # The main Home contains both accounts' registered chats; an isolated
    # Home contains only its owner's chats. Include older manual-only chats
    # created before manual sends began reusing the daily chat.
    rows=db.execute('''SELECT account_id,day,thread_id FROM account_daily_chats
        WHERE route=? AND day<? AND (?='main' OR account_id=?)
        UNION SELECT account_id,substr(scheduled,1,10),thread_id FROM account_runs
        WHERE route=? AND substr(scheduled,1,10)<? AND (?='main' OR account_id=?)
        AND alarm_id='' AND slot LIKE 'account-manual-v1:%' AND thread_id IS NOT NULL
        ORDER BY 2''',(route,day,route,record['id'],route,day,route,record['id'])).fetchall()
    deleted={(r['account_id'],r['thread_id']) for r in db.execute(
        'SELECT account_id,thread_id FROM account_chat_deletions WHERE route=?',(route,))}
    seen=set();result=[]
    for row in rows:
        key=(row['account_id'],row['thread_id'])
        if key not in seen and key not in deleted:result.append(row);seen.add(key)
    return result


def cleanup(record,rpc,route,day,current_thread):
    deadline=time.monotonic()+30
    with database(STATE) as db:rows=old_chats(db,record,route,day)
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
                           (old['account_id'],route,old['day'],thread))
                db.execute('INSERT OR IGNORE INTO account_chat_deletions VALUES(?,?,?,?)',
                           (old['account_id'],route,thread,now().isoformat()))
            log('account_old_chat_deleted',account=old['account_id'],route=route,thread=thread)
        except Exception:
            log('account_cleanup_deferred',account=record['id'],route=route,thread=thread)


def daily_chat(record,rpc,route,day):
    with database(STATE) as db:
        row=db.execute('SELECT thread_id FROM account_daily_chats WHERE account_id=? AND day=? AND route=?',
                       (record['id'],day,route)).fetchone()
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
    log('account_trigger_claimed',slot=slot,account=run['account_id'],
        scheduled=run['scheduled'],claimed_at=run['at'])
    if not 0<=(now()-scheduled).total_seconds()<=MAX_LATE:
        log('account_preparation_expired',slot=slot,account=run['account_id'],
            delay_seconds=(now()-scheduled).total_seconds())
        store.finish(STATE,run,'skipped_missed');return
    record=find_account(run['account_id'])
    if not record:
        store.finish(STATE,run,'failed_or_uncertain',error='指定账号已不存在');return
    lock=(STATE/('account-'+record['id']+'.lock')).open('a')
    rpc=None;route=None;thread=None;observed=None;day=now().date().isoformat()
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            store.finish(STATE,run,'skipped_busy');return
        rpc,route=open_account(STATE,record)
        for attempt in range(2):
            store.update(STATE,slot,'checking',route=route)
            if not matches(rpc,record):
                if route!='main':raise AccountChanged()
                rpc.close();rpc=None
                rpc=IsolatedRPC(STATE/'account-homes'/record['id'],record);route='isolated'
                continue
            thread,ready=daily_chat(record,rpc,route,day)
            # Main login can change during preparation. No turn has started:
            # selecting the isolated Home is still safe. Never retry a send.
            if matches(rpc,record):break
            if route!='main':raise AccountChanged()
            rpc.close();rpc=None
            rpc=IsolatedRPC(STATE/'account-homes'/record['id'],record);route='isolated'
        else:raise AccountChanged()
        store.update(STATE,slot,'checking',thread_id=thread)
        log('account_prepared',slot=slot,account=record['id'],route=route,thread=thread,
            delay_seconds=(now()-scheduled).total_seconds())
        if not ready:store.finish(STATE,run,'skipped_busy');return
        if not store.unchanged(STATE,run):store.finish(STATE,run,'skipped_changed');return
        if not 0<=(now()-scheduled).total_seconds()<=MAX_LATE:
            log('account_preparation_expired',slot=slot,account=record['id'],route=route,
                delay_seconds=(now()-scheduled).total_seconds())
            store.finish(STATE,run,'skipped_missed');return
        if not store.unchanged(STATE,run):store.finish(STATE,run,'skipped_changed');return
        store.update(STATE,slot,'sending',started_at=now().isoformat())
        observed=ObservedTurn(rpc,slot)
        proof=run_turn(observed,thread,slot,scheduled)
        store.finish(STATE,run,'completed',turn_id=proof['turn_id'],tokens=proof.get('tokens',14824),completed_at=now().isoformat())
        log('account_turn_verified',account=record['id'],route=route,thread=thread,turn_id=proof['turn_id'],tokens=proof.get('tokens',14824))
    except AccountChanged:
        store.finish(STATE,run,'skipped_account_changed',error='发送前无法核验目标账号，本次未发送')
    except SendMissed:
        store.finish(STATE,run,'skipped_missed')
        log('account_preparation_expired',slot=slot,account=record['id'],route=route,
            delay_seconds=(now()-scheduled).total_seconds())
    except Exception as error:
        reason=failure_reason(rpc,thread,observed,error)
        code=next((key for key,value in FAILURE_MESSAGES.items() if value==reason),'unknown')
        store.finish(STATE,run,'failed_or_uncertain',error=reason)
        log('account_turn_unconfirmed',slot=slot,account=record['id'],route=route,
            thread=thread,turn_id=observed.turn_id if observed else None,
            error_type=type(error).__name__,error_code=code)
    finally:
        try:
            if rpc:
                cleanup(record,rpc,route,day,thread)
                # Clear older chats in the other runtime as well, after sending.
                other='isolated' if route=='main' else 'main'
                with database(STATE) as db:
                    old=old_chats(db,record,other,day)
                if old:
                    other_rpc=None
                    try:
                        other_rpc=RPC() if other=='main' else IsolatedRPC(STATE/'account-homes'/record['id'],record)
                        cleanup(record,other_rpc,other,day,thread)
                    except Exception:log('account_cleanup_deferred',account=record['id'],route=other)
                    finally:
                        if other_rpc:other_rpc.close()
        except Exception:log('account_cleanup_deferred',account=record['id'],route=route,thread=thread)
        finally:
            try:
                if rpc:
                    if route=='isolated':
                        log('account_runtime_closing',slot=slot,account=record['id'],route=route,
                            thread=thread,turn_id=observed.turn_id if observed else None)
                    rpc.close()
            finally:lock.close()


if __name__=='__main__':process(sys.argv[1])
