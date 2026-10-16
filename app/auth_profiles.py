"""Native auth.json profile discovery and coherent credential refresh."""
import datetime as dt
import copy
import hashlib
import json
from pathlib import Path
import re
from account_quota import identity,public_id

from settings import CODEX_HOME as HOME


def read_auth(path):
    if path.is_symlink() or not path.is_file():raise ValueError('账号登录文件不存在')
    auth=json.loads(path.read_text())
    key,label=identity(auth)
    tokens=auth.get('tokens') or {}
    if not all(isinstance(tokens.get(k),str) and tokens[k] for k in ('id_token','access_token','refresh_token','account_id')):
        raise ValueError('账号登录文件不完整，请重新登录')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',key[0]):raise ValueError('账号标识无效')
    return {'id':public_id(key),'key':key,'name':label,'auth':auth,'path':path}


def profiles(home=None):
    home=Path(home or HOME);directory=home/'auth-profiles';result={}
    # Read only native long-lived profiles and the guide's bootstrap backups.
    paths=list(directory.glob('account-*.json'))+list(directory.glob('before-device-login-*.json'))
    for path in paths:
        try:
            account=read_auth(path)
            priority=(path.name.startswith('account-'),path.stat().st_mtime_ns)
            if account['id'] not in result or priority>result[account['id']]['priority']:
                result[account['id']]=account|{'priority':priority}
        except (OSError,ValueError,TypeError):continue
    return result


def canonical(home,auth):
    key,_=identity(auth)
    directory=Path(home)/'auth-profiles';path=directory/f'account-{key[0]}.json'
    if path.exists():
        try:
            if read_auth(path)['key']!=key:path=directory/f'account-{key[0]}-{public_id(key)}.json'
        except Exception:raise ValueError('长期登录副本格式异常，未覆盖') from None
    return path


def refreshed_native(profile):
    """Keep the native file shape, adopting a newer coherent saved OAuth bundle.

    A CLI/desktop-managed refresh may have superseded the guide's bootstrap
    copy. Never copy the CC Switch store itself into Codex's auth.json.
    """
    from account_quota import CC_STORE,TOKEN_STATE,SavedTokens,discover_accounts
    import time
    record=next((r for r in discover_accounts()[0] if r['key']==profile['key']),None)
    if not record or not record.get('managed_id'):return profile
    def native_stamp():
        try:return dt.datetime.fromisoformat(profile['auth']['last_refresh'].replace('Z','+00:00')).timestamp()*1000
        except (KeyError,ValueError,TypeError):return 0
    try:
        account=json.loads(CC_STORE.read_text())['accounts'][record['managed_id']]
        if account.get('token_updated_at_ms',0)<=native_stamp():return profile
    except (KeyError,OSError,ValueError,TypeError):return profile
    tokens=SavedTokens();access=tokens.access(record)
    for attempt in range(2):
        account=json.loads(CC_STORE.read_text())['accounts'][record['managed_id']]
        saved=json.loads(TOKEN_STATE.read_text()).get(record['id'],{})
        generation=hashlib.sha256(account['refresh_token'].encode()).hexdigest()
        if saved.get('access_token')==access and saved.get('generation')==generation and saved.get('expires',0)>time.time()+30:
            result=copy.deepcopy(profile)
            native=result['auth']
            native['tokens']={'account_id':account['chatgpt_account_id'],'id_token':account['id_token'],
                              'access_token':access,'refresh_token':account['refresh_token']}
            native['last_refresh']=dt.datetime.fromtimestamp(account['token_updated_at_ms']/1000,dt.timezone.utc).isoformat()
            if identity(native)[0]!=profile['key']:raise ValueError('Refreshed login identity differs')
            return result
        if attempt==0:access=tokens.access(record,force=True)
    raise ValueError('No coherent refreshed native credentials')
