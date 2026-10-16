import os
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

import account_control as control
import quota_watch as watch
from account_quota import atomic_json,identity,public_id
from auth_profiles import read_auth
from test_multi_account_quota import auth


class QuotaWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.home=root/'home';self.state=root/'state'
        (self.home/'auth-profiles').mkdir(parents=True)
        self.a=auth('alice');self.b=auth('bob')
        for name,value in [('alice',self.a),('bob',self.b)]:atomic_json(self.home/f'auth-profiles/account-{name}.json',value)
        atomic_json(self.home/'auth.json',self.a)
        self.aid=public_id(identity(self.a)[0]);self.bid=public_id(identity(self.b)[0]);self.now=time.time()
        self.runtime={'running':True,'workspace':'alice','email':'alice@example.com'}
        self.values={'accounts':[self.quota(self.aid,100),self.quota(self.bid,20)]}
        self.run=Mock();self.read=Mock(return_value=self.values);self.inspect=Mock(return_value=self.runtime)
        watch.configure(self.state,{'enabled':True})

    def quota(self,id,used):
        return {'id':id,'stale':False,'error':None,'updated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                'windows':[{'window_seconds':18000,'used_percent':used,'remaining_percent':100-used,'resets_at':self.now+3600},
                           {'window_seconds':604800,'used_percent':20,'remaining_percent':80,'resets_at':self.now+604800}]}

    def tick(self):watch.tick(self.state,self.home,self.read,self.inspect,performer=self.run,clock=lambda:self.now)

    def test_default_off_and_disabled_check_does_not_read_quota(self):
        self.assertFalse(watch.public_state(self.state/'new')['enabled'])
        watch.configure(self.state,{'enabled':False});self.tick();self.read.assert_not_called();self.run.assert_not_called()

    def test_settings_survive_reload_and_reject_other_fields(self):
        value=watch.configure(self.state,{'enabled':True});self.assertEqual(value,watch.public_state(self.state))
        for payload in ({'enabled':1},{'enabled':True,'model':'x'},{},[]):
            with self.assertRaises(ValueError):watch.configure(self.state,payload)

    def test_exhausted_current_queues_target_only_once(self):
        self.tick();job=control.load(self.state);self.assertEqual(job['account_id'],self.bid)
        self.assertIn('automatic',job);self.run.assert_called_once()
        job.update(status='failed');control.save(job,self.state)
        self.tick();self.run.assert_called_once()
        self.assertEqual(watch.public_state(self.state)['status'],'no_candidate')

    def test_available_current_never_switches(self):
        self.values['accounts'][0]=self.quota(self.aid,99);self.tick();self.run.assert_not_called()

    def test_weekly_exhaustion_alone_does_not_switch(self):
        self.values['accounts'][0]=self.quota(self.aid,20)
        self.values['accounts'][0]['windows'][1]['remaining_percent']=0
        self.tick();self.run.assert_not_called()

    def test_missing_stale_expired_and_wrong_window_current_never_switch(self):
        original=copy.deepcopy(self.values)
        for change in ('stale','error','old','reset','window','missing'):
            self.values=copy.deepcopy(original);self.read.return_value=self.values;a=self.values['accounts'][0]
            if change=='stale':a['stale']=True
            if change=='error':a['error']='offline'
            if change=='old':a['updated_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=5)).isoformat()
            if change=='reset':a['windows'][0]['resets_at']=self.now-1
            if change=='window':a['windows'][0]['window_seconds']=604800
            if change=='missing':a['windows']=[]
            self.tick();self.run.assert_not_called()

    def test_no_usable_alternative_never_switches(self):
        original=copy.deepcopy(self.values)
        for change in ('primary','weekly','stale','error','missing','unknown'):
            self.values=copy.deepcopy(original);self.read.return_value=self.values;b=self.values['accounts'][1]
            if change=='primary':b['windows'][0]['remaining_percent']=0
            if change=='weekly':b['windows'][1]['remaining_percent']=0
            if change=='stale':b['stale']=True
            if change=='error':b['error']='offline'
            if change=='missing':b['windows']=[]
            if change=='unknown':b['id']='c'*20
            self.tick();self.run.assert_not_called()

    def test_uses_running_identity_when_auth_file_points_elsewhere(self):
        atomic_json(self.home/'auth.json',self.b);self.tick()
        self.assertEqual(control.load(self.state)['automatic']['current'],self.aid)

    def test_busy_and_recent_completed_control_are_skipped(self):
        for status in ('starting','completed'):
            control.save({'status':status,'updated_at':control.stamp()},self.state);self.tick()
        self.inspect.assert_not_called();self.read.assert_not_called();self.run.assert_not_called()

    def test_disable_while_reading_prevents_queuing(self):
        def read():watch.configure(self.state,{'enabled':False});return self.values
        self.read.side_effect=read;self.tick();self.run.assert_not_called();self.assertIsNone(control.load(self.state))

    def test_worker_rechecks_disabled_setting_and_does_not_stop(self):
        self.tick();job=control.load(self.state);watch.configure(self.state,{'enabled':False})
        runner=Mock();control.perform(job['id'],self.state,self.home,runner)
        runner.assert_not_called();self.assertEqual(control.load(self.state)['status'],'cancelled')

    def test_worker_rechecks_runtime_and_quota_before_stop(self):
        self.tick();job=control.load(self.state)
        runner=Mock()
        with patch.object(control,'inspect_runtime',return_value={**self.runtime,'workspace':'bob'}):
            control.perform(job['id'],self.state,self.home,runner)
        runner.assert_not_called()
        self.assertEqual(control.load(self.state)['status'],'cancelled')

    def test_worker_quota_recovers_or_target_exhausts_cancels(self):
        for change in ('recover','exhaust','runtime_changes_during_read'):
            if control.load(self.state):
                # Explicit off/on is a new authorization generation for this test.
                watch.configure(self.state,{'enabled':False});watch.configure(self.state,{'enabled':True})
            self.tick();job=control.load(self.state);values=copy.deepcopy(self.values)
            if change=='recover':values['accounts'][0]=self.quota(self.aid,30)
            if change=='exhaust':values['accounts'][1]=self.quota(self.bid,100)
            runner=Mock();runtimes=[self.runtime,{**self.runtime,'workspace':'bob'}] if change=='runtime_changes_during_read' else None
            with patch.object(control,'inspect_runtime',side_effect=runtimes,return_value=self.runtime),patch.object(watch.cache,'get',return_value=values):
                control.perform(job['id'],self.state,self.home,runner)
            runner.assert_not_called();self.assertEqual(control.load(self.state)['status'],'cancelled')

    def test_automatic_worker_reuses_verified_switch_and_backup(self):
        self.tick();job=control.load(self.state);runner=Mock()
        def verify(home,key):
            self.assertEqual(read_auth(home/'auth.json')['key'],key)
            return {'email':key[2]}
        with patch.object(control,'inspect_runtime',return_value=self.runtime),patch.object(watch.cache,'get',return_value=self.values),patch.object(control,'refreshed_native',side_effect=lambda a:a):
            control.perform(job['id'],self.state,self.home,runner,verify)
        self.assertEqual(runner.call_args_list[0].args[0],'stop');self.assertEqual(runner.call_args_list[1].args[0],'start')
        self.assertEqual(control.load(self.state)['status'],'completed')
        self.assertEqual(read_auth(self.home/'auth.json')['id'],self.bid)

    def test_external_web_payload_cannot_supply_automatic_guard(self):
        with self.assertRaises(ValueError):control.request_operation({'action':'switch','confirmed':True,'automatic':{}},self.state,self.home)

    def test_reader_failure_keeps_account(self):
        self.read.side_effect=ConnectionError();self.tick();self.run.assert_not_called()
        self.assertEqual(watch.public_state(self.state)['status'],'unavailable')
