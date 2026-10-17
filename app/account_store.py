"""Durable claims for fixed ok messages bound to an account."""
import datetime as dt
import sqlite3
from schedule_store import database,list_alarms,now


def due_alarms(state,current=None):
    current=current or now();scheduled=current.replace(second=0,microsecond=0)
    return [a for a in list_alarms(state,current) if a['target_type']=='default' and a['account_id'] and a['enabled']
            and a['time']==scheduled.strftime('%H:%M') and (a['mode']=='daily' or dt.datetime.fromisoformat(a['run_at'])==scheduled)]


def reserve(state,alarm,scheduled):
    slot=f"account-v1:{alarm['account_id']}:{scheduled.isoformat()}"
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT * FROM alarms WHERE id=?',(alarm['id'],)).fetchone()
        if current is None or not current['enabled'] or current['revision']!=alarm['revision']:return None
        stamp=now().isoformat()
        try:
            db.execute('INSERT INTO account_runs(slot,alarm_id,revision,account_id,account_name,scheduled,status,at) VALUES(?,?,?,?,?,?,?,?)',
                       (slot,alarm['id'],alarm['revision'],alarm['account_id'],alarm['account_name'],scheduled.isoformat(),'dispatching',stamp))
        except sqlite3.IntegrityError:return None
        if alarm['mode']=='once':
            db.execute("UPDATE alarms SET enabled=0,consumed_at=?,last_status='pending' WHERE id=?",(stamp,alarm['id']))
        return slot


def begin(state,slot):
    with database(state) as db:
        db.execute('BEGIN IMMEDIATE')
        run=db.execute("SELECT * FROM account_runs WHERE slot=? AND status='dispatching'",(slot,)).fetchone()
        if run is None:return None
        alarm=db.execute('SELECT * FROM alarms WHERE id=?',(run['alarm_id'],)).fetchone()
        stamp=now().isoformat()
        if alarm is None or alarm['revision']!=run['revision'] or alarm['target_type']!='default' or alarm['account_id']!=run['account_id'] or (not alarm['enabled'] and alarm['consumed_at']!=run['at']):
            db.execute("UPDATE account_runs SET status='skipped_changed',at=? WHERE slot=?",(stamp,slot));return None
        db.execute("UPDATE account_runs SET status='checking',at=? WHERE slot=?",(stamp,slot))
        return dict(run)|{'alarm':dict(alarm),'consumed_at':alarm['consumed_at']}


def update(state,slot,status,**fields):
    allowed={'route','thread_id','turn_id','started_at','completed_at','error','tokens'}
    if set(fields)-allowed:raise ValueError('unknown execution fields')
    with database(state) as db:
        db.execute('UPDATE account_runs SET '+','.join(['status=?','at=?']+[f'{k}=?' for k in fields])+' WHERE slot=?',
                   (status,now().isoformat(),*fields.values(),slot))


def unchanged(state,run):
    with database(state) as db:
        row=db.execute('SELECT * FROM alarms WHERE id=?',(run['alarm_id'],)).fetchone()
        return row is not None and row['revision']==run['revision'] and row['target_type']=='default' and row['account_id']==run['account_id'] and (row['enabled'] or row['consumed_at']==run['consumed_at'])


def finish(state,run,status,**fields):
    update(state,run['slot'],status,**fields)
    if run['alarm']['mode']=='once':
        with database(state) as db:
            db.execute('UPDATE alarms SET last_status=? WHERE id=? AND revision=? AND consumed_at=?',
                       (status,run['alarm_id'],run['revision'],run['consumed_at']))
