"""Browse server work folders and create empty target chats once per request."""
import fcntl
import json
import os
from pathlib import Path
import uuid

from account_preferences import runtime_enabled
from account_quota import atomic_json
from rpc import RPC
from session_catalog import describe,list_models,managed_ids,_cache
from settings import WORKDIR


def roots():
    configured=os.environ.get('OKEEPER_WORKSPACE_ROOTS','')
    return [Path(p).expanduser().resolve() for p in configured.split(os.pathsep) if p] or [WORKDIR.resolve()]


def folder(value):
    if not isinstance(value,str) or not value or '\x00' in value:raise ValueError('请选择工作文件夹')
    try:path=Path(value).expanduser().resolve(strict=True)
    except (OSError,RuntimeError,ValueError):raise ValueError('文件夹不存在或暂时无法访问') from None
    allowed=False
    for root in roots():
        try:parts=path.relative_to(root).parts
        except ValueError:continue
        if not any(part.startswith('.') for part in parts):allowed=True;break
    if not allowed or not path.is_dir() or not os.access(path,os.R_OK|os.X_OK):
        raise ValueError('请选择允许范围内的可访问工作文件夹')
    return path


def browse(value=None):
    path=folder(value or str(roots()[0]));directories=[]
    try:
        for child in path.iterdir():
            if child.name.startswith('.'):continue
            try:
                valid=folder(str(child))
                directories.append({'name':child.name,'path':str(valid)})
            except ValueError:continue
    except OSError:raise ValueError('无法读取该文件夹，请选择其他位置') from None
    try:parent=str(folder(str(path.parent))) if path not in roots() else None
    except ValueError:parent=None
    return {'path':str(path),'parent':parent,'roots':[str(r) for r in roots()],
            'directories':sorted(directories,key=lambda item:item['name'].casefold())}


def create(state,payload):
    if not isinstance(payload,dict) or set(payload)-{'request_id','cwd','name','model','effort'}:
        raise ValueError('新建会话请求无效')
    try:
        id=payload['request_id']
        if str(uuid.UUID(id))!=id:raise ValueError()
    except (KeyError,ValueError,TypeError,AttributeError):raise ValueError('新建会话请求标识无效') from None
    path=folder(payload.get('cwd'))
    name=payload.get('name','').strip() if isinstance(payload.get('name',''),str) else None
    if name is None or len(name)>120 or any(ord(c)<32 for c in name):raise ValueError('会话名称最多 120 个字符，不能包含换行')
    model=payload.get('model','');effort=payload.get('effort','')
    if not isinstance(model,str) or not isinstance(effort,str) or (effort and not model):raise ValueError('模型或推理强度无效')
    directory=Path(state)/'session-create-receipts';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    receipt=directory/(id+'.json')
    with (directory/(id+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if receipt.exists():
            old=json.loads(receipt.read_text())
            if old.get('payload')!=payload:raise ValueError('请求标识已使用，请重新新建')
            if old.get('session'):return old['session']
            raise ValueError('上次新建结果未确认，请先刷新会话列表核对，避免重复创建')
        if model:
            selected=next((m for m in list_models() if m['id']==model),None)
            if not selected or (effort and effort not in selected['efforts']):raise ValueError('请选择当前可用的模型与推理强度')
        rpc=RPC()
        try:
            if not runtime_enabled(rpc,state):raise ValueError('当前主后台账号已停用，请先启用账号或切换后台')
            record={'payload':payload,'status':'creating'};atomic_json(receipt,record)
            params={'cwd':str(path),'ephemeral':False}
            if model:params['model']=model
            if effort:params['config']={'model_reasoning_effort':effort}
            result=rpc.call('thread/start',params)
            thread=result['thread']
            session=describe({**thread,'model':result.get('model') or thread.get('model'),
                'reasoningEffort':result.get('reasoningEffort') or thread.get('reasoningEffort')},managed_ids(state))
            session['name']=thread.get('name') or '新会话'
            record.update(status='created',session=session);atomic_json(receipt,record)
            if name:
                try:
                    rpc.call('thread/name/set',{'threadId':session['id'],'name':name})
                    session['name']=name
                except Exception:session['warning']='会话已创建，名称未保存，可稍后在 Codex 中修改'
            record['session']=session;atomic_json(receipt,record)
            _cache.pop(str(state),None)
            return session
        finally:rpc.close()
