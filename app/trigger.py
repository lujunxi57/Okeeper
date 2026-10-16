#!/usr/bin/env python3
"""Server-local daily chats. Durable claims, no queue, no retry/catch-up."""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo
from rpc import RPC
from schedule_store import enabled_times, claim_alarm, finish_alarm

from settings import WORKDIR
from settings import STATE
STATE.mkdir(parents=True, exist_ok=True)
TZ = ZoneInfo('Asia/Taipei')
MAX_LATE = 15
INSTRUCTIONS = '这是每日定时触发会话。用户输入仅为 ok 时，直接最终回复 ok 并结束本轮。不要调用工具、查询任务状态、等待本轮完成事件或创建定时任务。'

def now():
    return dt.datetime.now(TZ)

def log(event, **fields):
    record = {'at': now().isoformat(), 'event': event, **fields}
    with (STATE / 'events.jsonl').open('a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps(record, ensure_ascii=False), flush=True)

def save_json(name, data):
    temp = STATE / (name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(STATE / name)

def clean_old_chats(rpc, db, day, current_thread):
    """Only IDs registered by this scheduler; cleanup never retries an ok message."""
    deadline = time.monotonic() + 45
    rows = db.execute('SELECT day, thread_id FROM daily_chats WHERE day < ? ORDER BY day', (day,)).fetchall()
    if not rows:
        return
    inventory = {}
    for archived in (False, True):
        cursor = None
        while True:
            params = {'archived': archived, 'limit': 100}
            if cursor:
                params['cursor'] = cursor
            page = rpc.call('thread/list', params)
            for item in page['data']:
                inventory[item['id']] = (item, archived)
            cursor = page.get('nextCursor')
            if not cursor:
                break
    for old_day, old_thread in rows:
        if time.monotonic() >= deadline:
            log('cleanup_deferred', reason='time_budget', day=old_day, thread=old_thread)
            break
        if old_thread == current_thread:
            log('cleanup_deferred', reason='current_thread', day=old_day, thread=old_thread)
            continue
        try:
            entry = inventory.get(old_thread)
            if entry is None:
                # Both complete lists confirmed it is absent; retire stale bookkeeping.
                db.execute('DELETE FROM daily_chats WHERE day=? AND thread_id=?', (old_day, old_thread))
                db.commit()
                log('old_chat_already_absent', day=old_day, thread=old_thread)
                continue
            info, archived = entry
            if info['status']['type'] not in ('idle', 'notLoaded'):
                log('cleanup_deferred', reason='busy', day=old_day, thread=old_thread)
                continue
            if not archived:
                rpc.call('thread/archive', {'threadId': old_thread})
                log('old_chat_archived', day=old_day, thread=old_thread)
            rpc.call('thread/delete', {'threadId': old_thread})
            db.execute('DELETE FROM daily_chats WHERE day=? AND thread_id=?', (old_day, old_thread))
            db.commit()
            log('old_chat_deleted', day=old_day, thread=old_thread)
        except Exception as e:
            # Keep failed IDs registered, allowing cleanup on a later eligible slot.
            log('cleanup_failed', day=old_day, thread=old_thread, error=str(e))


def create_chat(rpc, title):
    result = rpc.call('thread/start', {
        'model': 'gpt-6-luna', 'cwd': str(WORKDIR),
        'config': {'model_reasoning_effort': 'low'},
        'developerInstructions': INSTRUCTIONS, 'ephemeral': False,
    })
    if result['model'] != 'gpt-6-luna' or result.get('reasoningEffort') != 'low':
        raise RuntimeError('new thread model/effort mismatch')
    thread = result['thread']['id']
    log('thread_created', thread=thread, title=title, model=result['model'], effort=result['reasoningEffort'])
    rpc.call('thread/name/set', {'threadId': thread, 'name': title})
    return thread

def run_turn(rpc, thread, key, scheduled):
    rpc.events.clear()
    sent = now()
    result = rpc.call('turn/start', {
        'threadId': thread,
        'input': [{'type': 'text', 'text': 'ok', 'text_elements': []}],
        'model': 'gpt-6-luna', 'effort': 'low',
        'clientUserMessageId': str(uuid.uuid5(uuid.NAMESPACE_URL, thread + key)),
    })
    turn = result['turn']['id']
    log('accepted', slot=key, thread=thread, turn_id=turn, text='ok')
    seen = {'started': False, 'user': False, 'final': False, 'completed': False}
    deadline = time.monotonic() + 180
    def consume(msg):
        method = msg.get('method')
        p = msg.get('params', {})
        if p.get('threadId') != thread:
            return
        if method == 'turn/started' and p.get('turn', {}).get('id') == turn:
            delay = (now() - scheduled).total_seconds()
            if delay > MAX_LATE:
                raise RuntimeError('turn started past delivery deadline')
            seen['started'] = True
            log('turn_started', slot=key, thread=thread, turn_id=turn, delay_seconds=delay)
        if method == 'item/completed' and p.get('turnId') == turn:
            item = p.get('item', {})
            if item.get('type') == 'userMessage':
                content = item.get('content', [])
                if len(content) == 1 and content[0].get('type') == 'text' and content[0].get('text') == 'ok':
                    seen['user'] = True
                    log('user_message_completed', slot=key, thread=thread, turn_id=turn, text='ok')
            if item.get('type') == 'agentMessage' and item.get('phase') != 'commentary':
                seen['final'] = True
                log('assistant_final', slot=key, thread=thread, turn_id=turn, text=item.get('text'))
        if method == 'turn/completed' and p.get('turn', {}).get('id') == turn:
            if p['turn']['status'] != 'completed':
                raise RuntimeError('turn ended: ' + p['turn']['status'])
            seen['completed'] = True
    for msg in rpc.events:
        consume(msg)
    while not seen['completed']:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('turn completion deadline exceeded')
        consume(rpc.recv(remaining))
    if not all(seen.values()):
        raise RuntimeError('missing live event evidence: ' + str(seen))
    data = rpc.call('thread/read', {'threadId': thread, 'includeTurns': True})['thread']
    matched = [t for t in data.get('turns', []) if t['id'] == turn]
    if data['id'] != thread or not matched or matched[0]['status'] != 'completed':
        raise RuntimeError('completed turn missing from same-thread history')
    items = matched[0].get('items', [])
    users = [i for i in items if i.get('type') == 'userMessage']
    finals = [i for i in items if i.get('type') == 'agentMessage' and i.get('phase') != 'commentary']
    if not any(len(i.get('content', [])) == 1 and i['content'][0].get('text') == 'ok' for i in users) or not finals:
        raise RuntimeError('messages missing from persisted history')
    return {'thread': thread, 'turn_id': turn, 'scheduled': scheduled.isoformat(),
            'sent': sent.isoformat(), 'completed': now().isoformat(),
            'model': 'gpt-6-luna', 'effort': 'low', 'live_events': seen,
            'persisted_same_thread': True, 'final_reply': finals[-1].get('text')}

def main():
    mode = sys.argv[1]
    n = now()
    if mode == 'test':
        scheduled = dt.datetime.fromisoformat(json.loads((STATE / 'test-schedule.json').read_text())['scheduled'])
    elif mode == 'test-reuse':
        scheduled = n
    elif mode == 'daily':
        past = [n.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in enabled_times(STATE, n)
                if n.replace(hour=h, minute=m, second=0, microsecond=0) <= n]
        if not past:
            return
        scheduled = max(past)
        # The minute dispatcher is idle except at an enabled alarm's exact minute.
        if (n.hour, n.minute) != (scheduled.hour, scheduled.minute):
            return
    else:
        raise ValueError('unknown mode')
    key = 'daily-v2:' + scheduled.isoformat() if mode == 'daily' else mode + '-v2:' + scheduled.isoformat()
    lock = (STATE / 'run.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('skipped_locked', slot=key); lock.close(); return
    db = sqlite3.connect(STATE / 'runs.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS runs (slot TEXT PRIMARY KEY, status TEXT, turn_id TEXT, at TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS daily_chats (day TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created_at TEXT NOT NULL)')
    try:
        db.execute('INSERT INTO runs VALUES (?,?,?,?)', (key, 'claimed', None, now().isoformat()))
        db.commit()
    except sqlite3.IntegrityError:
        log('skipped_duplicate', slot=key); db.close(); lock.close(); return
    thread = None
    fresh_chat = False
    claimed_alarm = None
    rpc = None
    def status(value, turn=None):
        db.execute('UPDATE runs SET status=?,turn_id=?,at=? WHERE slot=?', (value, turn, now().isoformat(), key))
        db.commit()
        log(value, slot=key, thread=thread, turn_id=turn)
    try:
        if not 0 <= (now() - scheduled).total_seconds() <= MAX_LATE:
            status('skipped_missed'); return
        if mode == 'daily' and not (STATE / 'daily-chat-test-passed.json').exists():
            status('skipped_unverified'); return
        rpc = RPC()
        if mode == 'test':
            thread = create_chat(rpc, '定时触发测试 ' + scheduled.strftime('%Y-%m-%d %H:%M'))
            fresh_chat = True
            save_json('test-chat.json', {'thread': thread, 'scheduled': scheduled.isoformat()})
        elif mode == 'test-reuse':
            thread = json.loads((STATE / 'test-chat.json').read_text())['thread']
        else:
            day = scheduled.date().isoformat()
            row = db.execute('SELECT thread_id FROM daily_chats WHERE day=?', (day,)).fetchone()
            if row:
                thread = row[0]
            else:
                # Any of the three slots can be today's first eligible trigger.
                thread = create_chat(rpc, '每日定时触发 ' + day)
                fresh_chat = True
                db.execute('INSERT INTO daily_chats VALUES (?,?,?)', (day, thread, now().isoformat()))
                db.commit()
        info = rpc.call('thread/read', {'threadId': thread})['thread']
        if not fresh_chat and 'archived_sessions' in Path(info.get('path') or '').parts:
            info = rpc.call('thread/unarchive', {'threadId': thread})['thread']
            log('current_chat_unarchived', thread=thread)
        if info['status']['type'] not in ('idle', 'notLoaded'):
            status('skipped_busy'); return
        if not fresh_chat:
            history = rpc.call('thread/read', {'threadId': thread, 'includeTurns': True})['thread']
            if history.get('turns') or info['status']['type'] == 'notLoaded':
                result = rpc.call('thread/resume', {'threadId': thread, 'excludeTurns': True})
                if result['thread']['id'] != thread or result['thread']['status']['type'] != 'idle':
                    status('skipped_busy'); return
        if (now() - scheduled).total_seconds() > MAX_LATE:
            status('skipped_missed'); return
        if mode == 'daily':
            claimed_alarm = claim_alarm(STATE, scheduled)
            if not claimed_alarm:
                status('skipped_disabled'); return
        proof = run_turn(rpc, thread, key, scheduled)
        status('verified_completed', proof['turn_id'])
        finish_alarm(STATE, claimed_alarm, 'completed')
        if mode == 'test':
            save_json('daily-chat-test-passed.json', proof)
        elif mode == 'test-reuse':
            save_json('daily-chat-reuse-test-passed.json', proof)
    except Exception as e:
        finish_alarm(STATE, claimed_alarm, 'failed')
        status('failed_or_uncertain')
        log('error', slot=key, thread=thread, error=str(e))
        raise
    finally:
        # Trigger first, then delete older registered chats so cleanup cannot delay
        # the time-critical ok message. Also retry previously deferred deletions.
        if mode == 'daily' and rpc and thread:
            try:
                clean_old_chats(rpc, db, scheduled.date().isoformat(), thread)
            except Exception as e:
                log('cleanup_failed', error=str(e))
        if rpc:
            rpc.close()
        db.close()
        lock.close()

if __name__ == '__main__':
    main()
