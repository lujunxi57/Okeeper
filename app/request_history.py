"""Read-only aggregation of Okeeper receipts, never session or credential scans."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
from turn_result import FAILURE_MESSAGES

LIMIT = 100
ACTIVE = {'claimed', 'dispatching', 'checking', 'creating', 'switching', 'sending', 'accepted', 'running',
          'waiting_input', 'observing_disconnected'}


def main_account_name(rpc):
    """Optional provenance; missing metadata must not authorize or block a send."""
    try:
        account = rpc.call('account/read', {'refreshToken': False}).get('account') or {}
        name = account.get('email')
        return name if account.get('type') == 'chatgpt' and isinstance(name, str) else None
    except Exception:
        return None


def timestamp(value):
    try:
        stamp = dt.datetime.fromisoformat(value)
        return stamp.timestamp() if stamp.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def outcome(status, kind, at, current):
    if status in {'completed', 'verified_completed'}:
        return 'completed', '已完成', '已收到本次发送的完成回执。'
    if kind == 'continue' and status == 'accepted':
        return 'accepted', '已提交', '已收到 continue 提交回执；原任务是否完成尚未确认。'
    # Long custom tasks may legitimately run or await approval indefinitely.
    expiry = 60 if kind == 'continue' else 315
    age = current - timestamp(at) if timestamp(at) is not None else None
    long_task = kind == 'custom' and status in {'accepted', 'running', 'waiting_input', 'observing_disconnected'}
    if status in ACTIVE and (long_task or (age is not None and age < expiry)):
        names = {'creating': '正在创建会话', 'switching': '正在切号', 'running': '执行中', 'waiting_input': '等待输入',
                 'observing_disconnected': '重新观察中', 'accepted': '已提交'}
        return 'pending', names.get(status, '发送中'), '尚未收到完成回执。'
    reasons = {'skipped_busy': '目标正在执行其他请求，本次未发送。',
               'skipped_missed': '已错过本次触发时间，未补发。',
               'skipped_changed': '任务设置已变化，本次未发送。',
               'skipped_account_changed': '发送前账号校验未通过，本次未发送。',
               'skipped_account_unavailable': '指定账号登录不可用，请重新授权；本次未发送。',
               'skipped_switch_failed': '指定账号切换未完成，本次未发送。',
               'skipped_settings': '无法确认需要沿用的会话设置，或设置正在变化；本次未发送。',
               'skipped_unverified': '默认触发尚未完成验证，本次未发送。',
               'skipped_disabled': '此任务已暂停或删除，本次未发送。'}
    if status.startswith('skipped'):
        return 'skipped', '未发送', reasons.get(status, '本次条件不满足，未发送。')
    return 'uncertain', '结果未确认', '未能确认本次发送结果；不会自动补发。'


def item(source, row, kind, current):
    at = row.get('scheduled') or row.get('at')
    state, label, result = outcome(row.get('status') or '', kind, row.get('at'), current)
    # Interpret known pre-submit failures from older workers without modifying
    # durable history or exposing arbitrary native error/credential text.
    if (source=='custom' and row.get('status')=='failed_or_uncertain' and not row.get('turn_id') and
        row.get('error') in {'无法确认会话当前的模型和思考强度，未发送','会话设置正在变化，未发送'}):
        state,label,result='skipped','未发送',row['error']+'。'
    if source=='account' and row.get('status')=='failed_or_uncertain':
        # Only our fixed safe messages may reach the UI; never relay raw errors.
        code=next((key for key,value in FAILURE_MESSAGES.items() if value==row.get('error')),None)
        if code:
            result=FAILURE_MESSAGES[code]+'。'
            label={'quota_exhausted':'额度不足','turn_interrupted':'已中断',
                   'turn_failed':'执行失败','preparation_failed':'准备失败',
                   'observation_timeout':'等待超时','observation_disconnected':'观察断开',
                   'verification_failed':'核验未通过','submission_uncertain':'提交未确认'}[code]
    route = row.get('route')
    if route not in {'main', 'isolated'}:
        confirmed_main = row.get('turn_id') or row.get('status') in {'completed', 'verified_completed', 'sending', 'accepted', 'running', 'waiting_input', 'observing_disconnected'}
        route = 'main' if source in {'default', 'custom', 'continue'} and state != 'skipped' and confirmed_main else None
    reason = {'automatic': '按计划自动触发', 'manual': '点击「立即发送一次」',
              'custom': '向指定会话定时发送', 'continue': '自动切号成功后恢复受影响的原会话'}[kind]
    return {'id': source + ':' + row['slot'], 'time': at, 'updated_at': row.get('at'),
            'kind': kind, 'account_id': row.get('account_id') or '',
            'account_name': row.get('account_name') or '', 'route': route,
            'status': state, 'status_label': label, 'result': result, 'reason': reason,
            'thread_id': row.get('thread_id') or '', 'thread_name': row.get('thread_name') or '',
            'turn_id': row.get('turn_id') or '',
            'message': row.get('prompt', 'continue' if kind == 'continue' else 'ok'),
            'model': row.get('model') or '', 'effort': row.get('effort') or ''}


def json_receipts(directory, limit):
    return sorted((p for p in directory.glob('*.json') if not p.is_symlink()),
                  key=lambda p: p.stat().st_mtime_ns, reverse=True)[:limit]


def recent_requests(state, current=None):
    state = Path(state)
    current = current if current is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    records = []
    partial = False
    path = state / 'runs.sqlite'
    if path.exists():
        # No migrations, status repairs, session RPCs or production mutations on GET.
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, source, order in [('account_runs', 'account', 'scheduled'),
                                         ('custom_runs', 'custom', 'scheduled'),
                                         ('runs', 'default', 'slot')]:
                if table not in tables:
                    continue
                where = "WHERE slot LIKE 'daily-v2:%'" if source == 'default' else ''
                for value in db.execute(f'SELECT * FROM {table} {where} ORDER BY {order} DESC LIMIT ?', (LIMIT,)):
                    row = dict(value)
                    if source == 'default':
                        row['scheduled'] = row['slot'][len('daily-v2:'):]
                    kind = 'manual' if source == 'account' and row['slot'].startswith('account-manual-v1:') else 'custom' if source == 'custom' else 'automatic'
                    records.append(item(source, row, kind, current))
        finally:
            db.close()
    # Legacy continuation receipts did not snapshot the target account. Match
    # their operation receipt when available; never infer from today's login.
    operations = {}
    paths = json_receipts(state / 'account-continue-receipts', LIMIT)
    if paths:
        for path in json_receipts(state / 'account-control-receipts', 500):
            try:
                value = json.loads(path.read_text())
                operations[value['id']] = (value.get('account_id') or '', value.get('account') or '')
            except (OSError, ValueError, KeyError, TypeError):
                continue
    for path in paths:
        try:
            row = json.loads(path.read_text())
            if (not isinstance(row, dict) or not isinstance(row.get('status'), str)
                    or not isinstance(row.get('at'), str) or not isinstance(row.get('thread_id'), str)):
                raise ValueError('invalid receipt')
            legacy_id, legacy_name = operations.get(row.get('operation_id'), ('', ''))
            row = {**row, 'slot': path.stem, 'route': 'main',
                   'account_id': row.get('account_id') or legacy_id,
                   'account_name': row.get('account_name') or legacy_name}
            records.append(item('continue', row, 'continue', current))
        except (OSError, ValueError, KeyError, TypeError):
            partial = True
    valid = [r for r in records if timestamp(r['time']) is not None]
    partial = partial or len(valid) != len(records)
    valid.sort(key=lambda r: (timestamp(r['time']), r['id']), reverse=True)
    return {'requests': valid[:LIMIT], 'limit': LIMIT, 'partial': partial}
