"""Offline browser fixture: no real accounts, tokens, sessions, or daemon calls."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'app'))
if not os.environ.get('OKEEPER_STATE_DIR'):raise RuntimeError('A disposable state directory is required')
import web_server as web

accounts=[{'id':'a'*20,'name':'morning@example.com','current':True},
          {'id':'b'*20,'name':'evening@example.com','current':False}]
sessions=[{'id':'00000000-0000-0000-0000-000000000001','name':'Example project alpha','cwd':'/projects/alpha','selectable':True,'archived':False,'reason':''},
          {'id':'00000000-0000-0000-0000-000000000002','name':'Example project beta','cwd':'/projects/beta','selectable':True,'archived':False,'reason':''},
          {'id':'00000000-0000-0000-0000-000000000003','name':'Managed daily trigger','cwd':'/projects/alpha','selectable':False,'archived':False,'reason':'自动触发会话不能选择'}]

web.account_catalog=lambda:[a for a in accounts if web.account_preferences.enabled(a['id'],web.STATE)]
web.profiles=lambda:{a['id']:a for a in accounts}
web.find_account=lambda id:next(({'id':a['id'],'label':a['name']} for a in web.account_catalog() if a['id']==id),None)
web.account_management.accounts=lambda state:[{**a,'enabled':web.account_preferences.enabled(a['id'],state)} for a in accounts]
class FakeLogin:
    value={'status':'idle'}
    def status(self):return dict(self.value)
    def start(self):
        self.value={'id':'fixture-login','status':'waiting','url':'https://auth.openai.com/codex/device','code':'TEST-1234'}
        return self.status()
    def cancel(self):self.value={'status':'cancelled'};return self.status()
web.account_management.login=FakeLogin()
workspace_root=web.STATE/'workspaces';workspace_root.mkdir()
(workspace_root/'项目 alpha').mkdir();(workspace_root/'project-beta').mkdir()
os.environ['OKEEPER_WORKSPACE_ROOTS']=str(workspace_root)
def create_session(state,payload):
    folder=web.workspace_sessions.folder(payload.get('cwd'))
    id='00000000-0000-4000-8000-'+str(len(sessions)+10).zfill(12)
    session={'id':id,'name':payload.get('name') or '新会话','cwd':str(folder),'selectable':True,'archived':False,'reason':'','model':'gpt-6.1-sol','effort':'high'}
    sessions.append(session);return session
web.workspace_sessions.create=create_session
web.list_sessions=lambda _:sessions
web.list_models=lambda **kwargs:[
    {'id':'gpt-6.1-sol','name':'GPT-6.1-Sol','default_effort':'high','efforts':['low','medium','high','xhigh','max','ultra']},
    {'id':'gpt-6-luna','name':'GPT-6-Luna','default_effort':'medium','efforts':['low','medium','high','xhigh','max']},
]
def validate(_,id):
    session=next((s for s in sessions if s['id']==id),None)
    if not session or not session['selectable']:raise ValueError('不能选择自动触发会话')
    return None,session
web.validate_target=validate
web.account_control.public_state=lambda _:{'operation':None,'busy':False,'runtime':{'running':True,'email':accounts[0]['name']},'accounts':[{**a,'switchable':True} for a in accounts]}
def forbidden(*args,**kwargs):raise ValueError('Fixture cannot restart a daemon')
web.account_control.request_operation=forbidden
web.account_dispatch.launch=forbidden
if os.environ.get('OKEEPER_FIXTURE_MANUAL')=='1':
    import threading
    def manual_launch(slot):
        run=web.account_store.begin(web.STATE,slot)
        if not run:return
        web.account_store.update(web.STATE,slot,'accepted',turn_id='fixture-turn')
        def complete():
            web.account_store.finish(web.STATE,run,'completed',tokens=12)
        threading.Timer(.5,complete).start()
    web.account_dispatch.launch=manual_launch
web.quota_cache=SimpleNamespace(get=lambda **kwargs:{'accounts':[],'error':None})
web.main()
