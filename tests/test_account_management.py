import base64
import copy
import datetime as dt
import json
import os
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

import account_management as management
import account_preferences as preferences
import account_quota as quota
import account_store
import account_control
import auth_profiles
import schedule_store
from test_multi_account_quota import auth


class FakeLoginClient:
    fail=False;wrong=False;wait=False;url='https://auth.openai.com/codex/device';instances=[]
    def __init__(self,home):
        self.home=Path(home);self.calls=[];self.events=[];self.closed=False
        self.__class__.instances.append(self)
    def call(self,method,params):
        self.calls.append((method,params))
        if method=='account/login/start':
            if self.fail:raise RuntimeError('secret upstream bearer token')
            if not self.wait:
                quota.atomic_json(self.home/'auth.json',auth('new-account'))
                self.events.append({'method':'account/login/completed','params':{'loginId':'login','success':True}})
            return {'loginId':'login','verificationUrl':self.url,'userCode':'TEST-1234'}
        if method=='account/read':return {'account':{'type':'chatgpt','email':'wrong@example.com' if self.wrong else 'new-account@example.com'},'workspaceRouting':{'chatgptAccountId':'new-account'}}
        raise AssertionError(method)
    def receive(self,timeout):
        time.sleep(.005);raise queue.Empty()
    def close(self):self.closed=True


class AccountManagementTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.state=self.root/'state';self.home=self.root/'home'
        self.state.mkdir();self.home.mkdir();quota.atomic_json(self.home/'auth.json',auth('original'))
        self.original=(self.home/'auth.json').read_bytes()
        FakeLoginClient.fail=FakeLoginClient.wrong=FakeLoginClient.wait=False
        FakeLoginClient.url='https://auth.openai.com/codex/device';FakeLoginClient.instances=[]
        self.patches=[patch.object(management,'NativeClient',FakeLoginClient),patch.object(quota,'AUTH',self.home/'auth.json'),
            patch.object(quota,'TOKEN_STATE',self.state/'access.json'),patch.object(quota,'STATE',self.state),patch.object(quota,'CC_DATABASE',self.root/'empty.db'),
            patch.object(quota,'CC_STORE',self.root/'empty.json'),patch.object(preferences,'STATE',self.state)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.login=management.LoginManager(self.state,self.home);self.addCleanup(self.login.cancel)
    def finish(self):
        self.login.start();self.login.worker.join(timeout=3)
        self.assertFalse(self.login.worker.is_alive());return self.login.status()

    def test_authorization_saves_validated_profile_without_touching_active_login(self):
        result=self.finish();self.assertEqual(result['status'],'completed')
        saved=auth_profiles.profiles(self.home)
        self.assertIn(result['account_id'],saved)
        self.assertEqual((self.home/'auth.json').read_bytes(),self.original)
        self.assertEqual(saved[result['account_id']]['path'].stat().st_mode&0o777,0o600)
        self.assertFalse(list(self.state.glob('account-login-*')))
        self.assertTrue(FakeLoginClient.instances[0].closed)
        self.assertNotIn('access_token',json.dumps(result));self.assertNotIn('refresh_token',json.dumps(result))
        self.assertIsNone(result['code']);self.assertIsNone(result['url'])

    def test_wrong_identity_and_failed_login_never_save_profiles(self):
        for mode in ('wrong','fail'):
            with self.subTest(mode=mode):
                setattr(FakeLoginClient,mode,True)
                result=self.finish();self.assertEqual(result['status'],'failed')
                self.assertEqual(auth_profiles.profiles(self.home),{})
                self.assertNotIn('secret',json.dumps(result));setattr(FakeLoginClient,mode,False)

    def test_untrusted_url_never_exposed_and_login_can_be_retried(self):
        FakeLoginClient.url='https://auth.openai.com.evil.invalid/login'
        result=self.finish();self.assertEqual(result['status'],'failed');self.assertIsNone(result.get('url'))
        FakeLoginClient.url='https://auth.openai.com/codex/device';self.assertEqual(self.finish()['status'],'completed')

    def test_cancel_keeps_existing_auth_and_closes_isolated_process(self):
        FakeLoginClient.wait=True;first=self.login.start()
        deadline=time.monotonic()+2
        while self.login.status()['status']=='starting' and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.login.start()['id'],first['id'])
        result=self.login.cancel();self.assertEqual(result['status'],'cancelled');self.assertIsNone(result['code'])
        self.assertEqual(auth_profiles.profiles(self.home),{});self.assertTrue(FakeLoginClient.instances[0].closed)
        self.assertEqual((self.home/'auth.json').read_bytes(),self.original)

    def test_login_twice_updates_same_account_and_does_not_enable_disabled_account(self):
        first=self.finish();preferences.configure(first['account_id'],{'enabled':False},self.state)
        second=self.finish();self.assertEqual(first['account_id'],second['account_id'])
        self.assertEqual(len(auth_profiles.profiles(self.home)),1)
        self.assertFalse(preferences.enabled(first['account_id'],self.state))

    def test_disable_filters_discovery_without_deleting_credentials_and_enable_restores(self):
        id=self.finish()['account_id'];before=auth_profiles.profiles(self.home)[id]['path'].read_bytes()
        preferences.configure(id,{'enabled':False},self.state)
        self.assertNotIn(id,{r['id'] for r in quota.discover_accounts()[0]})
        self.assertIn(id,{r['id'] for r in quota.discover_accounts(include_disabled=True)[0]})
        self.assertFalse(next(a for a in management.accounts(self.state) if a['id']==id)['enabled'])
        self.assertEqual(auth_profiles.profiles(self.home)[id]['path'].read_bytes(),before)
        preferences.configure(id,{'enabled':True},self.state)
        self.assertIn(id,{r['id'] for r in quota.discover_accounts()[0]})

    def test_disabling_account_hides_alarms_blocks_queued_and_manual_jobs_preserves_alarm(self):
        id=self.finish()['account_id'];record={'id':id,'label':'new-account@example.com'}
        current=dt.datetime(2026,10,24,6,59,tzinfo=schedule_store.TZ)
        with patch.object(schedule_store,'now',return_value=current):
            alarm_id=schedule_store.create_alarm(self.state,{'time':'07:01','account_id':id,'mode':'daily'})
        alarm=next(a for a in schedule_store.list_alarms(self.state) if a['id']==alarm_id)
        slot=account_store.reserve(self.state,alarm,current.replace(hour=7,minute=0))
        preferences.configure(id,{'enabled':False},self.state)
        self.assertIsNone(account_store.begin(self.state,slot))
        self.assertNotIn(alarm_id,{a['id'] for a in schedule_store.snapshot(self.state)['alarms']})
        self.assertEqual(account_store.due_alarms(self.state,current.replace(hour=7,minute=0)),[])
        self.assertIsNone(account_store.reserve(self.state,alarm,current.replace(hour=8,minute=0)))
        with self.assertRaises(ValueError):account_store.reserve_manual(self.state,record,'00000000-0000-4000-8000-000000000001')
        self.assertTrue(next(a for a in schedule_store.list_alarms(self.state) if a['id']==alarm_id)['enabled'])
        preferences.configure(id,{'enabled':True},self.state)
        self.assertIn(alarm_id,{a['id'] for a in schedule_store.snapshot(self.state)['alarms']})

    def test_disabled_account_cannot_become_switch_target(self):
        id=self.finish()['account_id'];preferences.configure(id,{'enabled':False},self.state)
        with self.assertRaises(ValueError):
            account_control.request_operation({'action':'switch','account_id':id,'confirmed':True,'request_id':'00000000-0000-4000-8000-000000000001'},self.state,self.home,Mock())

    def test_native_profile_refresh_uses_isolated_home_and_retains_active_login(self):
        id=self.finish()['account_id'];record=next(r for r in quota.discover_accounts()[0] if r['id']==id)
        new=auth('new-account');new['tokens']['access_token']='fresh-access';new['tokens']['refresh_token']='fresh-refresh'
        class RefreshClient:
            def __init__(self,home):self.home=Path(home)
            def call(self,method,params):
                self_test.assertEqual(params,{'refreshToken':True})
                quota.atomic_json(self.home/'auth.json',new)
                return {'account':{'email':'new-account@example.com'}}
            def close(self):pass
        self_test=self
        with patch('codex_stdio.NativeClient',RefreshClient),patch.object(quota,'STATE',self.state):
            result=quota.SavedTokens().access(record,force=True)
        self.assertEqual(result,'fresh-access')
        self.assertEqual(auth_profiles.profiles(self.home)[id]['auth']['tokens']['refresh_token'],'fresh-refresh')
        self.assertEqual((self.home/'auth.json').read_bytes(),self.original)

    def test_explicit_reauthorization_takes_priority_over_stale_cc_switch_copy(self):
        id=self.finish()['account_id'];native=auth_profiles.profiles(self.home)[id]['auth']
        quota.atomic_json(quota.CC_STORE,{'accounts':{'stale':{'chatgpt_account_id':'new-account','id_token':native['tokens']['id_token'],'refresh_token':'old-cc-refresh'}}})
        record=next(r for r in quota.discover_accounts()[0] if r['id']==id)
        self.assertEqual(record['source'],'profile');self.assertIsNone(record['managed_id'])
        self.assertEqual(record['auth']['tokens']['refresh_token'],native['tokens']['refresh_token'])

    def test_preference_validation_rejects_arbitrary_fields_and_non_bool(self):
        for payload in ({'enabled':0},{'enabled':True,'auth':'x'},{},[]):
            with self.assertRaises(ValueError):preferences.configure('a'*20,payload,self.state)

    def test_actual_stdio_worker_completes_official_protocol_with_fake_binary(self):
        from codex_stdio import NativeClient
        binary=Path(__file__).with_name('fake_login_codex.py')
        with patch.object(management,'NativeClient',NativeClient),patch('codex_stdio.CODEX_BIN',str(binary)):
            result=self.finish()
        self.assertEqual(result['status'],'completed');self.assertEqual(result['name'],'fixture@example.com')
        self.assertEqual((self.home/'auth.json').read_bytes(),self.original)
        self.assertEqual(len(auth_profiles.profiles(self.home)),1)
        self.assertFalse(list(self.state.glob('account-login-*')))
