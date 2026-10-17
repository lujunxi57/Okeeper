"""Send once to an existing chat and observe to completion, without a run limit."""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from websockets.exceptions import ConnectionClosed
from rpc import RPC
from schedule_store import now
from session_catalog import validate_target
import custom_store as store

from settings import STATE
MAX_LATE=15

def log(event,**fields):
    record=json.dumps({'at':now().isoformat(),'event':event,**fields},ensure_ascii=False)+'\n'
    STATE.mkdir(parents=True,exist_ok=True)
    fd=os.open(STATE/'events.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:os.write(fd,record.encode());os.fsync(fd)
    finally:os.close(fd)
    print(record,end='',flush=True)

def persisted_turn(rpc,thread,turn):
    cursor=None
    while True:
        params={'threadId':thread,'limit':100,'sortDirection':'desc','itemsView':'summary'}
        if cursor:params['cursor']=cursor
        page=rpc.call('thread/turns/list',params)
        for item in page['data']:
            if item['id']==turn:return item
        cursor=page.get('nextCursor')
        if not cursor:return None

def verify_message(rpc,thread,turn,prompt):
    cursor=None
    while True:
        params={'threadId':thread,'turnId':turn,'limit':100,'sortDirection':'asc'}
        if cursor:params['cursor']=cursor
        page=rpc.call('thread/items/list',params)
        for entry in page['data']:
            item=entry.get('item',entry)
            if item.get('type')=='userMessage':
                texts=[c.get('text','') for c in item.get('content',[]) if c.get('type')=='text']
                if ''.join(texts)==prompt:return True
        cursor=page.get('nextCursor')
        if not cursor:return False

def prepare(rpc,run):
    thread,meta=validate_target(STATE,run['thread_id'],rpc)
    if thread['status']['type'] not in ('idle','notLoaded'):return None,'skipped_busy'
    if meta['archived']:
        rpc.call('thread/unarchive',{'threadId':run['thread_id']})
        log('custom_thread_unarchived',slot=run['slot'],thread=run['thread_id'])
    # No model, effort, permissions, instructions or cwd overrides on resume/start.
    result=rpc.call('thread/resume',{'threadId':run['thread_id'],'excludeTurns':True})
    if result['thread']['id']!=run['thread_id']:raise RuntimeError('目标会话不一致')
    if result['thread']['status']['type']!='idle':return None,'skipped_busy'
    if not result.get('model') or not result.get('reasoningEffort'):
        raise RuntimeError('无法确认会话当前的模型和思考强度，未发送')
    latest=rpc.call('thread/read',{'threadId':run['thread_id']})['thread']
    if latest['status']['type']!='idle':return None,'skipped_busy'
    if latest.get('model')!=result['model'] or latest.get('reasoningEffort')!=result['reasoningEffort']:
        raise RuntimeError('会话设置正在变化，未发送')
    return result,None

def observe(rpc,run,turn):
    thread=run['thread_id'];slot=run['slot'];seen_started=False;last_status='accepted';last_poll=time.monotonic()
    while True:
        try:
            if rpc.events:
                message=rpc.events.pop(0)
            else:
                try:message=rpc.recv(30)
                except TimeoutError:message={}
            params=message.get('params',{})
            if params.get('threadId')==thread:
                method=message.get('method','')
                if method=='turn/started' and params.get('turn',{}).get('id')==turn:
                    seen_started=True;last_status='running'
                    store.update(STATE,slot,last_status,started_at=now().isoformat())
                    log('custom_turn_started',slot=slot,thread=thread,turn_id=turn)
                elif 'requestApproval' in method or 'requestUserInput' in method:
                    last_status='waiting_input';store.update(STATE,slot,last_status)
                    # Approval decisions remain with the conversation's existing reviewer/user.
                    log('custom_waiting_input',slot=slot,thread=thread,turn_id=turn)
                if method=='turn/completed' and params.get('turn',{}).get('id')==turn:
                    data=params['turn']
                    if data['status']!='completed':raise RuntimeError('本轮结束状态：'+data['status'])
                    stored=persisted_turn(rpc,thread,turn)
                    if not stored or stored['status']!='completed' or not verify_message(rpc,thread,turn,run['prompt']):
                        raise RuntimeError('会话历史中未确认本轮完成及原始提示词')
                    return
            if not message or time.monotonic()-last_poll>=30:
                last_poll=time.monotonic()
                stored=persisted_turn(rpc,thread,turn)
                if stored and stored['status']=='completed':
                    if not verify_message(rpc,thread,turn,run['prompt']):raise RuntimeError('未找到原始提示词')
                    return
                if stored and stored['status'] in ('failed','interrupted'):
                    raise RuntimeError('本轮结束状态：'+stored['status'])
                info=rpc.call('thread/read',{'threadId':thread})['thread']
                if info['status']['type']=='active':
                    flags=info['status'].get('activeFlags',[])
                    last_status='waiting_input' if any('waiting' in str(flag).lower() for flag in flags) else 'running'
                    if not seen_started:seen_started=True;store.update(STATE,slot,last_status,started_at=now().isoformat())
                    else:store.update(STATE,slot,last_status)
                elif stored and stored['status']=='inProgress':
                    raise RuntimeError('服务端已无活动执行，但历史结果不确定；不会重发')
        except (OSError,ConnectionError,ConnectionClosed) as error:
            log('custom_observer_disconnected',slot=slot,error=str(error));store.update(STATE,slot,'observing_disconnected')
            rpc.close()
            # Reattach only. Never resend or interrupt an already accepted task.
            while True:
                try:
                    rpc=RPC();rpc.call('thread/resume',{'threadId':thread,'excludeTurns':True});break
                except Exception:
                    try:rpc.close()
                    except Exception:pass
                    time.sleep(5)

def execute(slot):
    run=store.begin(STATE,slot)
    if run is None:return
    rpc=None;lock=None;turn=None
    try:
        scheduled=dt.datetime.fromisoformat(run['scheduled'])
        if not 0<=(now()-scheduled).total_seconds()<=MAX_LATE:
            store.finish(STATE,run,'skipped_missed');return
        lock=(STATE/('thread-'+hashlib.sha256(run['thread_id'].encode()).hexdigest()+'.lock')).open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            store.finish(STATE,run,'skipped_busy');return
        rpc=RPC();settings,skip=prepare(rpc,run)
        if skip:store.finish(STATE,run,skip);return
        if not store.unchanged(STATE,run):store.finish(STATE,run,'skipped_changed');return
        if (now()-scheduled).total_seconds()>MAX_LATE:store.finish(STATE,run,'skipped_missed');return
        alarm_cfg=run.get('alarm') or {}
        override_model=(alarm_cfg.get('custom_model') or '').strip()
        override_effort=(alarm_cfg.get('custom_effort') or '').strip()
        used_model=override_model or settings['model']
        used_effort=(override_effort or settings.get('reasoningEffort') or 'medium') if override_model else settings['reasoningEffort']
        store.update(STATE,slot,'sending',model=used_model,effort=used_effort)
        rpc.events.clear()
        turn_params={'threadId':run['thread_id'],
            'input':[{'type':'text','text':run['prompt'],'text_elements':[]}],
            'clientUserMessageId':str(uuid.uuid5(uuid.NAMESPACE_URL,slot))}
        if override_model:
            turn_params['model']=used_model
            turn_params['effort']=used_effort
        result=rpc.call('turn/start',turn_params)
        turn=result['turn']['id'];store.update(STATE,slot,'accepted',turn_id=turn)
        log('custom_accepted',slot=slot,thread=run['thread_id'],turn_id=turn,model=used_model,effort=used_effort)
        observe(rpc,run,turn)
        store.finish(STATE,run,'completed',completed_at=now().isoformat())
        log('custom_verified_completed',slot=slot,thread=run['thread_id'],turn_id=turn)
    except Exception as error:
        store.finish(STATE,run,'failed_or_uncertain',error=str(error),turn_id=turn)
        log('custom_failed_or_uncertain',slot=slot,thread=run['thread_id'],turn_id=turn,error=str(error))
    finally:
        if rpc:
            try:rpc.close()
            except Exception:pass
        if lock:lock.close()

if __name__=='__main__':execute(sys.argv[1])
