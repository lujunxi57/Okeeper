"""Account visibility/participation preferences; credentials remain untouched."""
import fcntl
import json
from pathlib import Path
import re

from settings import STATE


def disabled_ids(state=None):
    path=Path(state or STATE)/'account-preferences.json'
    try:value=json.loads(path.read_text())
    except FileNotFoundError:return set()
    if not isinstance(value,dict) or not isinstance(value.get('disabled'),list):
        raise ValueError('账号管理设置无法读取，请检查本机状态文件')
    return set(value['disabled'])


def enabled(account_id,state=None):
    return account_id not in disabled_ids(state)


def configure(account_id,payload,state=None):
    if not re.fullmatch(r'[a-f0-9]{20}',account_id) or not isinstance(payload,dict) or set(payload)!={'enabled'} or type(payload['enabled']) is not bool:
        raise ValueError('账号设置无效')
    from account_quota import atomic_json
    state=Path(state or STATE);state.mkdir(parents=True,exist_ok=True)
    with (state/'account-preferences.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        ids=disabled_ids(state)
        if payload['enabled']:ids.discard(account_id)
        else:ids.add(account_id)
        atomic_json(state/'account-preferences.json',{'disabled':sorted(ids)})
    return {'id':account_id,'enabled':payload['enabled']}


def runtime_enabled(rpc,state=None):
    disabled=disabled_ids(state)
    if not disabled:return True
    from account_quota import discover_accounts
    info=rpc.call('account/read',{'refreshToken':False})
    account=info.get('account') or {};workspace=(info.get('workspaceRouting') or {}).get('chatgptAccountId')
    for record in discover_accounts(include_disabled=True)[0]:
        if record['key'][0]==workspace and (record['key'][2] or '').casefold()==(account.get('email') or '').casefold():
            return record['id'] not in disabled
    return False
