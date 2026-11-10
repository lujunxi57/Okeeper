"""Continue affected root chats once after a verified automatic account switch.

Only metadata and turn receipts are persisted. Approval decisions, original
messages, model, effort, cwd and permission settings are never replaced.
"""
import datetime as dt
import hashlib
from pathlib import Path
import time
import uuid

from account_quota import atomic_json, public_id
from rpc import RPC
from session_catalog import is_managed, managed_ids

RECENT_SECONDS=300
CAPTURE_SECONDS=12
SEND_SECONDS=45
PREPARE_WAIT_SECONDS=5


def same_account(rpc,expected):
    value=rpc.call('account/read',{'refreshToken':False})
    account=value.get('account') or {}
    return (account.get('type')=='chatgpt' and expected[2] and
            (account.get('email') or '').casefold()==expected[2].casefold() and
            (value.get('workspaceRouting') or {}).get('chatgptAccountId')==expected[0])


def latest_turn(rpc,thread):
    page=rpc.call('thread/turns/list',{'threadId':thread,'limit':1,'sortDirection':'desc','itemsView':'notLoaded'})
    return next(iter(page.get('data') or []),None)


def quota_failure(turn):
    if not turn or turn.get('status')!='failed':return False
    info=(turn.get('error') or {}).get('codexErrorInfo')
    # Current local wire format is lower camel case; some clients expose the
    # enum variant name. Do not classify generic 429s or message substrings.
    return isinstance(info,str) and info in ('usageLimitExceeded','UsageLimitExceeded')


def eligible(thread,owned):
    return bool(thread.get('id') and not is_managed(thread,owned) and not thread.get('ephemeral') and
                not thread.get('parentThreadId') and thread.get('canAcceptDirectInput') is not False and
                thread.get('modelProvider')=='openai' and
                'subagent' not in str(thread.get('source','')).lower() and
                'archived_sessions' not in Path(thread.get('path') or '').parts)


def recent_failure(thread,turn,now):
    stamp=turn.get('completedAt') or thread.get('updatedAt')
    if isinstance(stamp,(int,float)) and not isinstance(stamp,bool):
        # Normalize seconds and milliseconds used by different server builds.
        stamp=stamp/1000 if stamp>100_000_000_000 else stamp
    else:return False
    return quota_failure(turn) and -5<=now-stamp<=RECENT_SECONDS


def capture(state,home,expected):
    """Take a pre-stop snapshot; failure here never prevents the account switch."""
    rpc=RPC(socket=str(Path(home)/'app-server-control/app-server-control.sock'))
    try:
        if not same_account(rpc,expected):raise RuntimeError('source account changed')
        deadline=time.monotonic()+CAPTURE_SECONDS
        owned=managed_ids(state);current=time.time();candidates=[];seen=set();incomplete=False
        # Includes recently failed chats that may already have unloaded.
        page=rpc.call('thread/list',{'archived':False,'limit':100,'sortKey':'updated_at',
                      'sourceKinds':['cli','vscode','exec','appServer','unknown'],'modelProviders':[]})
        summaries={t['id']:t for t in page.get('data',[])}
        loaded=rpc.call('thread/loaded/list',{}).get('data',[])
        ids=list(dict.fromkeys([*loaded,*summaries]))
        for thread_id in ids:
            if time.monotonic()>deadline:
                incomplete=True;break
            if thread_id in seen:continue
            seen.add(thread_id)
            try:
                thread=rpc.call('thread/read',{'threadId':thread_id})['thread']
                if not eligible(thread,owned):continue
                status=thread.get('status') or {}
                if status.get('activeFlags'):continue  # Includes waiting for user input/approval.
                turn=latest_turn(rpc,thread_id)
                if not turn or not turn.get('id'):continue
                active=status.get('type')=='active' and turn.get('status')=='inProgress'
                if active or recent_failure(thread,turn,current):
                    candidates.append({'thread_id':thread_id,'source_turn_id':turn['id'],
                                       'reason':'active_before_switch' if active else 'quota_failed'})
            except Exception:incomplete=True
        confirmed=[]
        for candidate in candidates:
            if candidate['reason']!='active_before_switch':
                confirmed.append(candidate);continue
            if time.monotonic()>deadline:
                incomplete=True;continue
            try:
                info=rpc.call('thread/read',{'threadId':candidate['thread_id']})['thread']
                latest=latest_turn(rpc,candidate['thread_id'])
                if not latest or latest.get('id')!=candidate['source_turn_id']:continue
                if (info.get('status',{}).get('type')=='active' and not info.get('status',{}).get('activeFlags') and
                    latest.get('status')=='inProgress'):
                    confirmed.append(candidate)
                elif quota_failure(latest):
                    confirmed.append({**candidate,'reason':'quota_failed'})
            except Exception:incomplete=True
        candidates=confirmed
        if page.get('nextCursor'):incomplete=True
        if not same_account(rpc,expected):raise RuntimeError('source account changed')
        return {'status':'captured','candidates':candidates,'incomplete':incomplete}
    finally:rpc.close()


def still_authorized(state,generation):
    from quota_watch import config
    setting=config(state)
    return setting['enabled'] and setting['continue_tasks'] and setting['generation']==generation


def receipt_path(state,candidate):
    digest=hashlib.sha256((candidate['thread_id']+':'+candidate['source_turn_id']).encode()).hexdigest()
    return Path(state)/'account-continue-receipts'/(digest+'.json')


def resumable(turn,candidate):
    if not turn or turn.get('id')!=candidate['source_turn_id']:return False
    return quota_failure(turn) or (candidate['reason']=='active_before_switch' and turn.get('status')=='interrupted')


def idle_source(rpc,candidate,owned):
    """Shutdown/interrupt acknowledgement can precede the final idle event.

    Wait briefly for this exact source turn only; never interrupt or steer it.
    A new turn, waiting-input flag or completed result is left untouched.
    """
    deadline=time.monotonic()+PREPARE_WAIT_SECONDS
    while True:
        thread=rpc.call('thread/read',{'threadId':candidate['thread_id']})['thread']
        if not eligible(thread,owned):return None
        status=thread.get('status') or {}
        turn=latest_turn(rpc,candidate['thread_id'])
        if not turn or turn.get('id')!=candidate['source_turn_id'] or status.get('activeFlags'):return None
        if status.get('type') in ('idle','notLoaded'):
            return thread if resumable(turn,candidate) else None
        if status.get('type')!='active' or time.monotonic()>=deadline:return None
        time.sleep(.2)


def resume(state,home,expected,generation,plan,operation_id):
    """Called under account-control lock, after target login is verified.

    Persist sending BEFORE turn/start. A lost response is uncertain, never
    automatically retried. Accepted means submission, not task completion.
    """
    results=[];rpc=None;deadline=time.monotonic()+SEND_SECONDS
    try:
        owned=managed_ids(state)
        for candidate in plan.get('candidates',[]):
            result={**candidate,'status':'skipped'}
            results.append(result)
            if not still_authorized(state,generation):
                result['reason']='disabled';continue
            if time.monotonic()>deadline:
                result['reason']='deadline';continue
            path=receipt_path(state,candidate)
            if path.exists():
                result['reason']='already_attempted';continue
            try:
                if rpc is None:rpc=RPC(socket=str(Path(home)/'app-server-control/app-server-control.sock'))
                if not same_account(rpc,expected):
                    result['reason']='account_changed';continue
                thread_id=candidate['thread_id']
                thread=idle_source(rpc,candidate,owned)
                if thread is None:
                    result['reason']='unavailable_or_busy';continue
                settings=rpc.call('thread/resume',{'threadId':thread_id,'excludeTurns':True})
                if settings['thread']['id']!=thread_id or settings['thread'].get('status',{}).get('type')!='idle':
                    result['reason']='unavailable_or_busy';continue
                if not settings.get('model') or not settings.get('reasoningEffort'):
                    result['reason']='settings_unverified';continue
                # A user may have continued/cancelled while restart or resume
                # was in progress. Check again immediately before submission.
                latest=rpc.call('thread/read',{'threadId':thread_id})['thread']
                if (not eligible(latest,owned) or latest.get('status',{}).get('type')!='idle' or
                    latest.get('model')!=settings['model'] or latest.get('reasoningEffort')!=settings['reasoningEffort'] or
                    not resumable(latest_turn(rpc,thread_id),candidate)):
                    result['reason']='turn_changed';continue
                if not still_authorized(state,generation) or not same_account(rpc,expected):
                    result['reason']='authorization_changed';continue
                from account_preferences import runtime_enabled
                if not runtime_enabled(rpc,state):
                    result['reason']='account_disabled';continue
                result.update(status='sending',operation_id=operation_id,
                              at=dt.datetime.now(dt.timezone.utc).isoformat(),
                              account_id=public_id(expected),account_name=expected[2],route='main',
                              thread_name=thread.get('name') or thread.get('title') or '')
                atomic_json(path,result)
                value=rpc.call('turn/start',{'threadId':thread_id,
                    'input':[{'type':'text','text':'continue','text_elements':[]}],
                    'clientUserMessageId':str(uuid.uuid5(uuid.NAMESPACE_URL,'okeeper-continue:'+path.stem))})
                turn=value.get('turn') or {}
                if not turn.get('id'):raise RuntimeError('missing turn receipt')
                result.update(status='accepted',turn_id=turn['id'])
                atomic_json(path,result)
            except Exception:
                result['status']='uncertain' if path.exists() else 'failed'
                result['reason']='submission_unconfirmed' if path.exists() else 'prepare_failed'
                if path.exists():atomic_json(path,result)
                if rpc:
                    try:rpc.close()
                    except Exception:pass
                    rpc=None
    finally:
        if rpc:
            try:rpc.close()
            except Exception:pass
    return {'status':'submitted','results':results,'accepted':sum(r['status']=='accepted' for r in results),
            'skipped':sum(r['status']=='skipped' for r in results),
            'unconfirmed':sum(r['status'] in ('uncertain','failed') for r in results),
            'incomplete':plan.get('incomplete',False)}
