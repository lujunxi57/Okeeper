"""Durable, per-target occurrence claims for custom messages."""
import datetime as dt
import json
import sqlite3
from schedule_store import database,list_alarms,now

def due_alarms(state,current=None):
    from account_preferences import disabled_ids
    disabled=disabled_ids(state)
    current=current or now();scheduled=current.replace(second=0,microsecond=0)
    return [a for a in list_alarms(state,current) if a['target_type']=='thread' and a['account_id'] not in disabled and a['enabled'] and a['time']==scheduled.strftime('%H:%M')
            and (a['mode']=='daily' or dt.datetime.fromisoformat(a['run_at'])==scheduled)]

def reserve(state,alarm,scheduled):
    from account_preferences import enabled
    if not enabled(alarm.get('account_id',''),state):return None
    slot=f"{'new-thread-v1' if alarm.get('new_session') else 'thread-v1'}:{alarm['thread_id']}:{scheduled.isoformat()}"
    with database(state) as db:
        try:
            db.execute('INSERT INTO custom_runs(slot,alarm_id,revision,thread_id,scheduled,prompt,status,at,thread_name,account_id,account_name) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (slot,alarm['id'],alarm['revision'],'' if alarm.get('new_session') else alarm['thread_id'],scheduled.isoformat(),alarm['prompt'],'dispatching',now().isoformat(),alarm.get('thread_name',''),alarm.get('account_id',''),alarm.get('account_name','')))
        except sqlite3.IntegrityError:return None
    return slot

def begin(state,slot):
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        run=db.execute("SELECT * FROM custom_runs WHERE slot=? AND status='dispatching'",(slot,)).fetchone()
        if run is None:return None
        alarm=db.execute('SELECT * FROM alarms WHERE id=?',(run['alarm_id'],)).fetchone()
        if alarm is None or not alarm['enabled'] or alarm['target_type']!='thread' or alarm['revision']!=run['revision']:
            db.execute("UPDATE custom_runs SET status='skipped_changed',at=? WHERE slot=?",(now().isoformat(),slot));return None
        from account_preferences import enabled
        if not enabled(alarm['account_id'],state):
            db.execute("UPDATE custom_runs SET status='skipped_disabled',at=? WHERE slot=?",(now().isoformat(),slot));return None
        stamp=now().isoformat()
        db.execute("UPDATE custom_runs SET status='checking',at=? WHERE slot=?",(stamp,slot))
        if alarm['mode']=='once':
            db.execute("UPDATE alarms SET enabled=0,consumed_at=?,last_status='pending' WHERE id=? AND revision=?",(stamp,alarm['id'],alarm['revision']))
        cfg=dict(alarm);cfg['new_session']=json.loads(cfg['new_session']) if cfg['new_session'] else None
        return dict(run)|{'alarm':cfg,'consumed_at':stamp if alarm['mode']=='once' else None}

def update(state,slot,status,**fields):
    allowed={'turn_id','thread_id','thread_name','model','effort','error','started_at','completed_at','account_name','route','account_id'}
    if set(fields)-allowed:raise ValueError('unknown execution fields')
    with database(state) as db:
        assignments=['status=?','at=?']+[f'{key}=?' for key in fields]
        db.execute('UPDATE custom_runs SET '+','.join(assignments)+' WHERE slot=?',(status,now().isoformat(),*fields.values(),slot))

def finish(state,run,status,**fields):
    update(state,run['slot'],status,**fields)
    alarm=run['alarm']
    if alarm['mode']=='once':
        with database(state) as db:
            db.execute('UPDATE alarms SET last_status=? WHERE id=? AND revision=? AND consumed_at=?',
                       (status,alarm['id'],run['revision'],run['consumed_at']))

def unchanged(state,run):
    from account_preferences import enabled
    if not enabled((run.get('alarm') or {}).get('account_id',''),state):return False
    with database(state) as db:
        row=db.execute('SELECT revision,target_type,enabled,consumed_at FROM alarms WHERE id=?',(run['alarm_id'],)).fetchone()
        return row is not None and row['revision']==run['revision'] and row['target_type']=='thread' and (row['enabled'] or row['consumed_at']==run['consumed_at'])
