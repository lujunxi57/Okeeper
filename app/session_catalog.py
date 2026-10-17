"""Metadata only: enumerate every project on this server, never create chats."""
import re
import threading
import time
from pathlib import Path
from rpc import RPC
from schedule_store import database

SOURCES=['cli','vscode','exec','appServer','subAgent','subAgentReview','subAgentCompact','subAgentThreadSpawn','subAgentOther','unknown']
_cache={}
_models_cache=None
_lock=threading.Lock()

def managed_ids(state):
    with database(state) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_chats'").fetchone()
        owned={r[0] for r in db.execute('SELECT thread_id FROM daily_chats')} if exists else set()
        owned.update(r[0] for r in db.execute("SELECT thread_id FROM account_daily_chats WHERE route='main'"))
        return owned

def is_managed(thread,owned):
    return thread['id'] in owned or bool(re.match(r'^(?:每日定时触发 \d{4}-\d{2}-\d{2}|定时触发测试 )',thread.get('name') or ''))

def describe(thread,owned,archived=False):
    reason='自动触发会话不可选' if is_managed(thread,owned) else '此会话不支持直接发送' if thread.get('canAcceptDirectInput') is False or thread.get('ephemeral') else ''
    return {'id':thread['id'],'name':thread.get('name') or (thread.get('preview') or '未命名会话').splitlines()[0][:120],
            'cwd':thread.get('cwd',''),'archived':archived,'status':thread.get('status',{}).get('type','notLoaded'),
            'model':thread.get('model') or '','effort':thread.get('reasoningEffort') or '',
            'selectable':not reason,'reason':reason}

def list_sessions(state, *, refresh=False):
    key=str(state)
    with _lock:
        cached=_cache.get(key)
        if cached and not refresh and time.monotonic()-cached[0]<10:
            return cached[1]
        rpc=RPC()
        try:
            owned=managed_ids(state);items={}
            for archived in (False,True):
                cursor=None
                while True:
                    params={'archived':archived,'limit':100,'sourceKinds':SOURCES,'modelProviders':[],'sortKey':'updated_at'}
                    if cursor:params['cursor']=cursor
                    page=rpc.call('thread/list',params)
                    for thread in page['data']:items[thread['id']]=describe(thread,owned,archived)
                    cursor=page.get('nextCursor')
                    if not cursor:break
            result=list(items.values());_cache[key]=(time.monotonic(),result)
            return result
        finally:rpc.close()

def validate_target(state,thread_id,rpc=None):
    own_rpc=rpc is None
    rpc=rpc or RPC()
    try:
        thread=rpc.call('thread/read',{'threadId':thread_id})['thread']
        meta=describe(thread,managed_ids(state),'archived_sessions' in Path(thread.get('path') or '').parts)
        if not meta['selectable']:raise ValueError(meta['reason'])
        return thread,meta
    finally:
        if own_rpc:rpc.close()

def list_models(*, refresh=False):
    global _models_cache
    with _lock:
        if _models_cache and not refresh and time.monotonic()-_models_cache[0]<30:
            return _models_cache[1]
        rpc=RPC()
        try:
            page=rpc.call('model/list',{})
            models=[]
            for item in page.get('data',[]):
                if item.get('hidden'):continue
                mid=item.get('model') or item.get('id') or ''
                if not mid:continue
                efforts=[e.get('reasoningEffort') for e in item.get('supportedReasoningEfforts',[]) if e.get('reasoningEffort')]
                models.append({
                    'id':mid,
                    'name':item.get('displayName') or mid,
                    'default_effort':item.get('defaultReasoningEffort') or (efforts[0] if efforts else 'medium'),
                    'efforts':efforts or ['low','medium','high'],
                })
            _models_cache=(time.monotonic(),models)
            return models
        finally:
            rpc.close()
