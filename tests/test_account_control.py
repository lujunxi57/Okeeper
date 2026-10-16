import os
import json
from pathlib import Path
import tempfile
import unittest
import uuid
import time
from unittest.mock import Mock,patch

import account_control as control
from auth_profiles import profiles,read_auth
from test_multi_account_quota import auth
from auth_profiles import refreshed_native
import account_quota as quota


class AccountControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.home=root/'home';self.state=root/'state'
        (self.home/'auth-profiles').mkdir(parents=True)
        self.a=auth('alice');self.b=auth('bob')
        (self.home/'auth.json').write_text(json.dumps(self.a))
        (self.home/'auth-profiles/account-bob.json').write_text(json.dumps(self.b))
        self.b_id=next(iter(profiles(self.home)))

    def request(self,action='switch',**more):
        payload={'action':action,'confirmed':True,'request_id':str(uuid.uuid4())}
        if action=='switch':payload['account_id']=self.b_id
        payload.update(more)
        return payload

    def queue(self,action='switch'):
        return control.request_operation(self.request(action),self.state,self.home,lambda _:None)

    def verifier(self,home,key):
        self.assertEqual(read_auth(home/'auth.json')['key'],key)
        return {'running':True,'email':key[2],'workspace':key[0]}

    def test_stop_precedes_backup_and_latest_refreshed_bundle_is_saved(self):
        job=self.queue();fresh=auth('alice');fresh['tokens']['refresh_token']='refreshed-during-work'
        calls=[]
        def daemon(action,home):
            calls.append(action)
            if action=='stop':(home/'auth.json').write_text(json.dumps(fresh))
            if action=='start':self.assertEqual(read_auth(home/'auth.json')['auth'],self.b)
        control.perform(job['id'],self.state,self.home,daemon,self.verifier)
        self.assertEqual(calls,['stop','start'])
        self.assertEqual(read_auth(self.home/'auth-profiles/account-alice.json')['auth'],fresh)
        backups=list((self.home/'auth-profiles').glob('before-switch-*.json'));self.assertEqual(len(backups),1)
        self.assertEqual(read_auth(backups[0])['auth'],fresh)
        self.assertEqual((self.home/'auth.json').stat().st_mode&0o777,0o600)
        self.assertEqual(control.load(self.state)['status'],'completed')
        self.assertNotIn('refresh_token',json.dumps(control.load(self.state)))

    def test_failed_target_verification_restores_original_before_restarting(self):
        job=self.queue();calls=[]
        def daemon(action,home):calls.append((action,read_auth(home/'auth.json')['key'][0]))
        def verify(home,key):
            if key[0]=='bob':raise RuntimeError('Credentials rejected')
            return self.verifier(home,key)
        control.perform(job['id'],self.state,self.home,daemon,verify)
        self.assertEqual(calls,[('stop','alice'),('start','bob'),('stop','bob'),('start','alice')])
        self.assertEqual(read_auth(self.home/'auth.json')['auth'],self.a)
        self.assertEqual(control.load(self.state)['status'],'failed')
        self.assertIn('恢复原账号',control.load(self.state)['message'])

    def test_failed_stop_never_changes_credentials_or_tries_to_activate(self):
        job=self.queue();runner=Mock(side_effect=RuntimeError())
        control.perform(job['id'],self.state,self.home,runner,self.verifier)
        runner.assert_called_once_with('stop',self.home)
        self.assertEqual(read_auth(self.home/'auth.json')['auth'],self.a)
        self.assertIn('未修改',control.load(self.state)['message'])

    def test_stop_errors_preserve_allowlisted_reason_without_raw_cli_output(self):
        job=self.queue();runner=Mock(side_effect=control.DaemonCommandError('stop',1,'unmanaged_runtime'))
        control.perform(job['id'],self.state,self.home,runner,self.verifier)
        value=control.load(self.state)
        self.assertEqual(value['diagnostic'],{'step':'stop','code':'unmanaged_runtime','exit_code':1})
        self.assertIn('进程登记不一致',value['message'])
        self.assertEqual(read_auth(self.home/'auth.json')['auth'],self.a)

    def test_known_unmanaged_error_is_classified(self):
        self.assertEqual(control.classify_daemon_error('Error: app server is running but is not managed by codex app-server daemon'),'unmanaged_runtime')
        self.assertEqual(control.classify_daemon_error('arbitrary upstream secret'),'daemon_command_failed')

    def test_restart_keeps_account_and_saves_a_fresh_native_profile(self):
        job=self.queue('restart');calls=[]
        control.perform(job['id'],self.state,self.home,lambda a,h:calls.append(a),self.verifier)
        self.assertEqual(calls,['stop','start']);self.assertEqual(read_auth(self.home/'auth.json')['auth'],self.a)
        self.assertEqual(control.load(self.state)['status'],'completed')

    def test_repeated_post_and_worker_execution_do_not_restart_twice(self):
        payload=self.request();launched=[]
        first=control.request_operation(payload,self.state,self.home,launched.append)
        second=control.request_operation(payload,self.state,self.home,launched.append)
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(launched),1)
        runner=Mock();control.perform(first['id'],self.state,self.home,runner,self.verifier)
        control.perform(first['id'],self.state,self.home,runner,self.verifier)
        self.assertEqual(runner.call_count,2)
        again=control.request_operation(payload,self.state,self.home,launched.append)
        self.assertEqual(again['status'],'completed');self.assertEqual(len(launched),1)

    def test_unconfirmed_request_and_arbitrary_path_or_command_are_rejected(self):
        for changes in ({'confirmed':False},{'path':'/etc/passwd'},{'command':'restart'},{'action':'shell'},{'account_id':'unknown'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):control.request_operation(self.request(**changes),self.state,self.home,lambda _:None)

    def test_another_pending_operation_is_rejected_without_queuing(self):
        self.queue()
        with self.assertRaises(BlockingIOError):control.request_operation(self.request(),self.state,self.home,lambda _:None)

    def test_missing_profile_detected_before_stopping_backend(self):
        job=self.queue();(self.home/'auth-profiles/account-bob.json').unlink();runner=Mock()
        control.perform(job['id'],self.state,self.home,runner,self.verifier)
        runner.assert_not_called();self.assertEqual(read_auth(self.home/'auth.json')['auth'],self.a)

    def test_symlink_profiles_are_not_accepted(self):
        (self.home/'auth-profiles/account-link.json').symlink_to(self.home/'auth.json')
        self.assertEqual(len(profiles(self.home)),1)

    def test_long_lived_profile_is_preferred_to_bootstrap_copy(self):
        old=auth('bob');old['tokens']['refresh_token']='old-bootstrap'
        (self.home/'auth-profiles/before-device-login-20261017.json').write_text(json.dumps(old))
        self.assertEqual(profiles(self.home)[self.b_id]['auth'],self.b)

    def test_launch_failure_leaves_auth_intact_and_marks_failed(self):
        runner=Mock(side_effect=RuntimeError('secret upstream output'))
        value=control.request_operation(self.request(),self.state,self.home,runner)
        self.assertEqual(value['status'],'failed');self.assertNotIn('secret',json.dumps(value))
        self.assertEqual(read_auth(self.home/'auth.json')['auth'],self.a)

    def test_newer_saved_oauth_bundle_is_converted_to_native_shape_with_identity_check(self):
        profile=profiles(self.home)[self.b_id];record={'key':profile['key'],'id':profile['id'],'managed_id':'saved-bob'}
        cc=self.state/'cc.json';cache=self.state/'access.json';self.state.mkdir()
        access=self.b['tokens']['access_token'];refresh='newest-refresh'
        import hashlib
        cc.write_text(json.dumps({'accounts':{'saved-bob':{'chatgpt_account_id':'bob','id_token':self.b['tokens']['id_token'],
                                                          'refresh_token':refresh,'token_updated_at_ms':int(time.time()*1000)}}}))
        cache.write_text(json.dumps({profile['id']:{'access_token':access,'generation':hashlib.sha256(refresh.encode()).hexdigest(),'expires':time.time()+3600}}))
        with patch.object(quota,'CC_STORE',cc),patch.object(quota,'TOKEN_STATE',cache),patch.object(quota,'discover_accounts',return_value=([record],None)),patch.object(quota,'SavedTokens') as saved:
            saved.return_value.access.return_value=access
            result=refreshed_native(profile)
        self.assertEqual(result['auth']['tokens']['refresh_token'],refresh)
        self.assertEqual(result['auth']['tokens']['account_id'],'bob')
        self.assertEqual(result['key'],profile['key']);self.assertEqual(profile['auth']['tokens']['refresh_token'],'refresh-bob')


if __name__=='__main__':unittest.main()
