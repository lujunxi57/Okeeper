"""Persistent, editable daily and one-shot alarms for the local dispatcher."""
import datetime as dt
from contextlib import contextmanager
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')
DEFAULT_TIMES = ('07:00', '12:05', '17:10')
MAX_PROMPT = 32768

def now():
    return dt.datetime.now(TZ)

def connect(state):
    state.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state / 'runs.sqlite', timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('CREATE TABLE IF NOT EXISTS alarm_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS alarms (id TEXT PRIMARY KEY, time TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), created_at TEXT NOT NULL)')
    db.execute('BEGIN IMMEDIATE')
    columns = {r['name'] for r in db.execute('PRAGMA table_info(alarms)')}
    additions = {'mode': "TEXT NOT NULL DEFAULT 'daily' CHECK(mode IN ('daily','once'))",
                 'run_at': 'TEXT', 'consumed_at': 'TEXT', 'last_status': 'TEXT',
                 'revision': 'INTEGER NOT NULL DEFAULT 0',
                 'target_type': "TEXT NOT NULL DEFAULT 'default' CHECK(target_type IN ('default','thread'))",
                 'thread_id': "TEXT NOT NULL DEFAULT ''", 'thread_name': "TEXT NOT NULL DEFAULT ''",
                 'prompt': "TEXT NOT NULL DEFAULT ''", 'account_id': "TEXT NOT NULL DEFAULT ''",
                 'account_name': "TEXT NOT NULL DEFAULT ''",
                 'custom_model': "TEXT NOT NULL DEFAULT ''", 'custom_effort': "TEXT NOT NULL DEFAULT ''"}
    for column, definition in additions.items():
        if column not in columns:
            db.execute(f'ALTER TABLE alarms ADD COLUMN {column} {definition}')
    # Replace the legacy global UNIQUE(time), preserving every existing record.
    for index in db.execute('PRAGMA index_list(alarms)').fetchall():
        names = [r['name'] for r in db.execute('SELECT name FROM pragma_index_info(?)', (index['name'],))]
        if index['unique'] and names in (['time'], ['time','target_type','thread_id']):
            db.execute('''CREATE TABLE alarms_upgrade (
                id TEXT PRIMARY KEY, time TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                created_at TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'daily' CHECK(mode IN ('daily','once')),
                run_at TEXT, consumed_at TEXT, last_status TEXT, revision INTEGER NOT NULL DEFAULT 0,
                target_type TEXT NOT NULL DEFAULT 'default' CHECK(target_type IN ('default','thread')),
                thread_id TEXT NOT NULL DEFAULT '',thread_name TEXT NOT NULL DEFAULT '',prompt TEXT NOT NULL DEFAULT '',
                account_id TEXT NOT NULL DEFAULT '',account_name TEXT NOT NULL DEFAULT '',
                custom_model TEXT NOT NULL DEFAULT '',custom_effort TEXT NOT NULL DEFAULT '',
                UNIQUE(time,target_type,thread_id,account_id))''')
            fields='id,time,enabled,created_at,mode,run_at,consumed_at,last_status,revision,target_type,thread_id,thread_name,prompt,account_id,account_name,custom_model,custom_effort'
            db.execute(f'INSERT INTO alarms_upgrade ({fields}) SELECT {fields} FROM alarms')
            db.execute('DROP TABLE alarms')
            db.execute('ALTER TABLE alarms_upgrade RENAME TO alarms')
            break
    db.execute('CREATE TABLE IF NOT EXISTS runs (slot TEXT PRIMARY KEY, status TEXT, turn_id TEXT, at TEXT, tokens INTEGER)')
    if 'tokens' not in {r['name'] for r in db.execute('PRAGMA table_info(runs)')}:
        db.execute('ALTER TABLE runs ADD COLUMN tokens INTEGER')
    db.execute('''CREATE TABLE IF NOT EXISTS custom_runs (
        slot TEXT PRIMARY KEY, alarm_id TEXT NOT NULL, revision INTEGER NOT NULL, thread_id TEXT NOT NULL,
        scheduled TEXT NOT NULL,prompt TEXT NOT NULL,status TEXT NOT NULL,at TEXT NOT NULL,
        turn_id TEXT,model TEXT,effort TEXT,error TEXT,started_at TEXT,completed_at TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS custom_runs_alarm ON custom_runs(alarm_id,scheduled DESC)')
    db.execute('''CREATE TABLE IF NOT EXISTS account_runs (
        slot TEXT PRIMARY KEY, alarm_id TEXT NOT NULL, revision INTEGER NOT NULL, account_id TEXT NOT NULL,
        account_name TEXT NOT NULL,scheduled TEXT NOT NULL,status TEXT NOT NULL,at TEXT NOT NULL,
        route TEXT,thread_id TEXT,turn_id TEXT,started_at TEXT,completed_at TEXT,error TEXT,tokens INTEGER)''')
    if 'tokens' not in {r['name'] for r in db.execute('PRAGMA table_info(account_runs)')}:
        db.execute('ALTER TABLE account_runs ADD COLUMN tokens INTEGER')
    db.execute('CREATE INDEX IF NOT EXISTS account_runs_alarm ON account_runs(alarm_id,scheduled DESC)')
    db.execute('''CREATE TABLE IF NOT EXISTS account_daily_chats (
        account_id TEXT NOT NULL,day TEXT NOT NULL,route TEXT NOT NULL,thread_id TEXT NOT NULL,
        created_at TEXT NOT NULL,PRIMARY KEY(account_id,day,route))''')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS account_daily_one_chat ON account_daily_chats(account_id,day)')
    if db.execute("SELECT value FROM alarm_settings WHERE key='initialized'").fetchone() is None:
        stamp = now().isoformat()
        db.executemany('INSERT INTO alarms (id,time,enabled,created_at) VALUES (?,?,1,?)',
                       [(str(uuid.uuid4()), time, stamp) for time in DEFAULT_TIMES])
        db.execute("INSERT INTO alarm_settings VALUES ('initialized','1')")
    db.commit()
    return db

@contextmanager
def database(state):
    db = connect(state)
    try:
        with db:
            yield db
    finally:
        db.close()

def valid_time(value):
    if not isinstance(value, str) or re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) is None:
        raise ValueError('请选择有效的时间')
    return value

def valid_mode(value):
    if value not in ('daily', 'once'):
        raise ValueError('请选择每天或仅一次')
    return value

def next_occurrence(time, current=None):
    current = current or now()
    hour, minute = map(int, time.split(':'))
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += dt.timedelta(days=1)
    return target.isoformat()

def expire_once(db, current):
    # A missed occurrence is retired, never shifted to tomorrow automatically.
    cutoff = current.replace(second=0, microsecond=0).isoformat()
    db.execute("UPDATE alarms SET enabled=0,last_status='missed',consumed_at=?,revision=revision+1 WHERE mode='once' AND enabled=1 AND run_at < ?",
               (current.isoformat(), cutoff))

def list_alarms(state, current=None):
    current = current or now()
    with database(state) as db:
        expire_once(db, current)
        return [dict(row) | {'enabled': bool(row['enabled'])} for row in db.execute('SELECT * FROM alarms ORDER BY time')]

def enabled_times(state, current=None):
    current = current or now()
    return tuple(tuple(map(int, a['time'].split(':'))) for a in list_alarms(state, current)
                 if a['target_type'] == 'default' and not a['account_id'] and a['enabled'] and (a['mode'] == 'daily' or dt.datetime.fromisoformat(a['run_at']).date() == current.date()))

def delivery_fields(payload, old=None, thread_name=None):
    old = old or {}
    target = payload.get('target_type', old.get('target_type', 'default'))
    if target not in ('default','thread'):
        raise ValueError('请选择发送方式')
    if target == 'default':
        if payload.get('prompt') or payload.get('thread_id'):
            raise ValueError('默认触发固定发送 ok，不能指定会话或提示词')
        return target, '', '', ''
    thread = payload.get('thread_id', old.get('thread_id',''))
    prompt = payload.get('prompt', old.get('prompt',''))
    if not isinstance(thread,str) or re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',thread) is None:
        raise ValueError('请选择有效的 Codex 会话')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>MAX_PROMPT or '\x00' in prompt:
        raise ValueError(f'请填写提示词，最多 {MAX_PROMPT} 个字符')
    name = thread_name if thread_name is not None else old.get('thread_name','') if thread==old.get('thread_id') else ''
    return target, thread, str(name)[:512], prompt

ALARM_FIELDS={'time','enabled','mode','target_type','thread_id','prompt','account_id','custom_model','custom_effort'}

def custom_model_fields(payload, old=None):
    old = old or {}
    target = payload.get('target_type', old.get('target_type', 'default'))
    if target == 'default':
        if payload.get('custom_model') or payload.get('custom_effort'):
            raise ValueError('默认触发固定模型，不能自定义模型或思考强度')
        return '', ''
    model = payload.get('custom_model', old.get('custom_model', ''))
    effort = payload.get('custom_effort', old.get('custom_effort', ''))
    if not isinstance(model, str) or not isinstance(effort, str):
        raise ValueError('模型或思考强度格式无效')
    model, effort = model.strip(), effort.strip()
    if not model:
        return '', ''
    if re.fullmatch(r'[a-zA-Z0-9._-]{1,64}', model) is None:
        raise ValueError('请选择有效的模型')
    if effort and re.fullmatch(r'[a-zA-Z0-9_-]{1,32}', effort) is None:
        raise ValueError('请选择有效的思考强度')
    return model, effort

def account_fields(payload,old=None,account_name=None):
    old=old or {};target=payload.get('target_type',old.get('target_type','default'))
    account=payload.get('account_id',old.get('account_id','') if target=='default' else '')
    if not isinstance(account,str) or (account and re.fullmatch(r'[a-f0-9]{20}',account) is None):
        raise ValueError('请选择有效的 Codex 账号')
    if target=='thread' and account:raise ValueError('指定会话沿用原后台账号，不能另选账号')
    name=account_name if account_name is not None else old.get('account_name','') if account==old.get('account_id') else ''
    return account,str(name)[:512] if account else ''

def avoid_ambiguous_default(db,time,target,account,exclude=''):
    if target=='default' and db.execute("SELECT 1 FROM alarms WHERE time=? AND target_type='default' AND id!=? AND ((account_id='' AND ?!='') OR (account_id!='' AND ?='')) LIMIT 1",
                                       (time,exclude,account,account)).fetchone():
        raise ValueError('同一时刻已有默认触发，请先为它指定账号，避免重复发送')

def alarm_enabled(state, scheduled):
    return (scheduled.hour, scheduled.minute) in enabled_times(state, now())

def create_alarm(state, payload, *, thread_name=None, account_name=None):
    if not isinstance(payload, dict) or set(payload) - ALARM_FIELDS:
        raise ValueError('只能设置时间、重复方式与启停状态')
    time = valid_time(payload.get('time'))
    mode = valid_mode(payload.get('mode', 'daily'))
    enabled = payload.get('enabled', True)
    target, thread, name, prompt = delivery_fields(payload,thread_name=thread_name)
    account,email = account_fields(payload,account_name=account_name)
    c_model, c_effort = custom_model_fields(payload)
    if type(enabled) is not bool:
        raise ValueError('启停状态无效')
    with database(state) as db:
        alarm_id = str(uuid.uuid4())
        avoid_ambiguous_default(db,time,target,account)
        db.execute('INSERT INTO alarms (id,time,enabled,created_at,mode,run_at,target_type,thread_id,thread_name,prompt,account_id,account_name,custom_model,custom_effort) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (alarm_id, time, int(enabled), now().isoformat(), mode, next_occurrence(time) if mode == 'once' else None,target,thread,name,prompt,account,email,c_model,c_effort))
    return alarm_id

def advance_alarm(state,alarm_id):
    """Clone a default alarm +301 minutes, preserving an absolute one-shot date."""
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM alarms WHERE id=?',(alarm_id,)).fetchone()
        if row is None:raise LookupError('这个时间已被删除')
        if row['target_type']!='default':raise ValueError('仅默认触发可以添加五小时窗口')
        current=now()
        base=(dt.datetime.fromisoformat(row['run_at']) if row['mode']=='once' else
              dt.datetime.fromisoformat(next_occurrence(row['time'],current)))
        target=base+dt.timedelta(minutes=301)
        if row['mode']=='once' and target<=current:
            raise ValueError('延后后的时间已过，请修改原时间后再添加')
        time=target.strftime('%H:%M');alarm_id=str(uuid.uuid4())
        avoid_ambiguous_default(db,time,'default',row['account_id'])
        db.execute('INSERT INTO alarms (id,time,enabled,created_at,mode,run_at,target_type,account_id,account_name) VALUES (?,?,1,?,?,?,?,?,?)',
                   (alarm_id,time,current.isoformat(),row['mode'],target.isoformat() if row['mode']=='once' else None,
                    'default',row['account_id'],row['account_name']))
    return alarm_id


def update_alarm(state, alarm_id, payload, *, thread_name=None, account_name=None):
    if not isinstance(payload, dict) or not payload or set(payload) - ALARM_FIELDS:
        raise ValueError('只能设置时间、重复方式与启停状态')
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM alarms WHERE id=?', (alarm_id,)).fetchone()
        if row is None:
            raise LookupError('这个时间已被删除')
        time = valid_time(payload.get('time', row['time']))
        mode = valid_mode(payload.get('mode', row['mode']))
        enabled = payload.get('enabled', bool(row['enabled']))
        target, thread, name, prompt = delivery_fields(payload,dict(row),thread_name)
        account,email = account_fields(payload,dict(row),account_name)
        c_model, c_effort = custom_model_fields(payload, dict(row))
        avoid_ambiguous_default(db,time,target,account,alarm_id)
        if type(enabled) is not bool:
            raise ValueError('启停状态无效')
        changed = bool(set(payload)&{'time','mode','target_type','thread_id','prompt','account_id','custom_model','custom_effort'})
        rearmed = enabled and (not row['enabled'] or row['consumed_at'] is not None or
                              (row['run_at'] and dt.datetime.fromisoformat(row['run_at']) <= now()))
        run_at = (next_occurrence(time) if changed or rearmed else row['run_at']) if mode == 'once' else None
        clear_result = changed or rearmed or mode == 'daily'
        db.execute('UPDATE alarms SET time=?,mode=?,enabled=?,run_at=?,consumed_at=?,last_status=?,target_type=?,thread_id=?,thread_name=?,prompt=?,account_id=?,account_name=?,custom_model=?,custom_effort=?,revision=revision+1 WHERE id=?',
                   (time, mode, int(enabled), run_at,
                    None if clear_result else row['consumed_at'], None if clear_result else row['last_status'],target,thread,name,prompt,account,email,c_model,c_effort,alarm_id))

def toggle_alarm(state, alarm_id, payload):
    # Backward-compatible helper; HTTP PATCH also supports time and mode edits.
    if not isinstance(payload, dict) or set(payload) != {'enabled'}:
        raise ValueError('只能修改启停状态')
    update_alarm(state, alarm_id, payload)

def claim_alarm(state, scheduled):
    """Consume one-shots atomically BEFORE inference, including uncertain attempts."""
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM alarms WHERE time=? AND enabled=1 AND target_type='default' AND account_id=''", (scheduled.strftime('%H:%M'),)).fetchone()
        if row is None:
            return None
        if row['mode'] == 'once' and dt.datetime.fromisoformat(row['run_at']) != scheduled:
            return None
        alarm = dict(row)
        if row['mode'] == 'once':
            consumed = now().isoformat()
            db.execute("UPDATE alarms SET enabled=0,consumed_at=?,last_status='pending' WHERE id=? AND revision=?", (consumed, row['id'], row['revision']))
            alarm['consumed_at'] = consumed
        return alarm

def finish_alarm(state, alarm, status):
    if alarm and alarm['mode'] == 'once':
        with database(state) as db:
            # A user edit/rearm during inference belongs to the next occurrence.
            db.execute('UPDATE alarms SET last_status=? WHERE id=? AND revision=? AND consumed_at=?',
                       (status, alarm['id'], alarm['revision'], alarm['consumed_at']))

def delete_alarm(state, alarm_id):
    with database(state) as db:
        result = db.execute('DELETE FROM alarms WHERE id=?', (alarm_id,))
        if result.rowcount != 1:
            raise LookupError('这个时间已被删除')

def trigger_stats(state, current=None):
    day_prefix = (current or now()).date().isoformat()
    with database(state) as db:
        r1 = db.execute("SELECT COUNT(*), COALESCE(SUM(COALESCE(tokens, 14824)), 0) FROM runs WHERE status='verified_completed'").fetchone()
        r2 = db.execute("SELECT COUNT(*), COALESCE(SUM(COALESCE(tokens, 14824)), 0) FROM account_runs WHERE status='completed'").fetchone()
        t1 = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(COALESCE(tokens, 14824)), 0) FROM runs WHERE status='verified_completed' AND (slot LIKE ? OR at LIKE ?)",
            (f'%{day_prefix}%', f'{day_prefix}%')
        ).fetchone()
        t2 = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(COALESCE(tokens, 14824)), 0) FROM account_runs WHERE status='completed' AND (scheduled LIKE ? OR at LIKE ?)",
            (f'{day_prefix}%', f'{day_prefix}%')
        ).fetchone()
        return {
            'trigger_count': int(r1[0] + r2[0]),
            'trigger_tokens': int(r1[1] + r2[1]),
            'today_count': int(t1[0] + t2[0]),
            'today_tokens': int(t1[1] + t2[1]),
        }

def snapshot(state):
    current = now()
    alarms = list_alarms(state, current)
    for alarm in alarms:
        alarm['next_at'] = ((next_occurrence(alarm['time'], current) if alarm['mode'] == 'daily' else alarm['run_at'])
                            if alarm['enabled'] else None)
        if alarm['target_type']=='thread':
            with database(state) as db:
                run=db.execute('SELECT status,at,error,model,effort FROM custom_runs WHERE alarm_id=? AND revision=? ORDER BY scheduled DESC LIMIT 1',(alarm['id'],alarm['revision'])).fetchone()
                alarm['last_run']=dict(run) if run else None
        elif alarm['account_id']:
            with database(state) as db:
                run=db.execute('SELECT status,at,route,error FROM account_runs WHERE alarm_id=? AND revision=? ORDER BY scheduled DESC LIMIT 1',(alarm['id'],alarm['revision'])).fetchone()
                alarm['last_run']=dict(run) if run else None
    upcoming = sorted((a for a in alarms if a['enabled']), key=lambda a: a['next_at'])
    return {'alarms': alarms, 'now': current.isoformat(), 'timezone': 'Asia/Taipei',
            'next': upcoming[0] if upcoming else None, 'model': '6 Luna', 'effort': 'Light', 'message': 'ok',
            'stats': trigger_stats(state, current)}
