"""Kernel peer identity and exact-process termination on disposable Unix servers."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import owned_runtime as runtime

SERVER='''
import signal,socket,sys
if 'ignore' in sys.argv:signal.signal(signal.SIGTERM,signal.SIG_IGN)
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.bind(sys.argv[-1]);s.listen()
print('ready',flush=True)
while True:
 c,_=s.accept()
 with c:
  while c.recv(4):c.sendall(b'pong')
'''


class Client:
    def __init__(self,socket):
        self.connection=globals()['socket'].socket(globals()['socket'].AF_UNIX,globals()['socket'].SOCK_STREAM)
        self.connection.settimeout(2);self.connection.connect(socket)
        self.ws=SimpleNamespace(socket=self.connection)
    def call(self,*args):
        self.connection.sendall(b'ping')
        if self.connection.recv(4)!=b'pong':raise ConnectionError()
        return {}
    def close(self):self.connection.close()


class OwnedRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='okeeper-peer-',dir='/tmp');self.addCleanup(temp.cleanup)
        self.home=Path(temp.name);(self.home/'app-server-control').mkdir()
        self.path=self.home/'app-server-control/app-server-control.sock'
        self.processes=[];self.addCleanup(self.cleanup)

    def cleanup(self):
        for child in self.processes:
            if child.poll() is None:child.kill()
            child.wait(timeout=3);child.stdout.close()

    def launch(self,*args):
        child=subprocess.Popen([sys.executable,'-u','-c',SERVER,'app-server',*args,str(self.path)],stdout=subprocess.PIPE,text=True)
        self.processes.append(child);self.assertEqual(child.stdout.readline().strip(),'ready');return child

    def test_kernel_peer_matches_listener_and_only_that_process_is_stopped(self):
        child=self.launch('--listen','unix://');other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdout=subprocess.PIPE,text=True);self.processes.append(other)
        client=Client(str(self.path))
        try:self.assertEqual(runtime.identity(client,sys.executable)[0],child.pid)
        finally:client.close()
        runtime.stop(self.home,sys.executable,rpc_factory=Client,timeout=2)
        self.assertEqual(child.wait(timeout=2),-signal.SIGTERM);self.assertIsNone(other.poll())

    def test_wrong_executable_or_proxy_never_gets_signalled(self):
        child=self.launch('proxy')
        for binary in ('/bin/false',sys.executable):
            with self.subTest(binary=binary),self.assertRaises(runtime.StopError) as raised:
                runtime.stop(self.home,binary,rpc_factory=Client,timeout=.1)
            self.assertEqual(raised.exception.category,'unverified_runtime');self.assertIsNone(child.poll())

    def test_different_user_or_reused_identity_never_gets_signalled(self):
        child=self.launch()
        with patch.object(runtime.os,'getuid',return_value=os.getuid()+1),self.assertRaises(runtime.StopError):
            runtime.stop(self.home,sys.executable,rpc_factory=Client,timeout=.1)
        with patch.object(runtime,'identity',side_effect=[(child.pid,'original'),(child.pid,'replacement')]),self.assertRaises(runtime.StopError):
            runtime.stop(self.home,sys.executable,rpc_factory=Client,timeout=.1)
        self.assertIsNone(child.poll())

    def test_timeout_never_escalates_to_force_kill(self):
        child=self.launch('ignore')
        with self.assertRaises(runtime.StopError) as raised:runtime.stop(self.home,sys.executable,rpc_factory=Client,timeout=.05)
        self.assertEqual(raised.exception.category,'daemon_timeout');self.assertIsNone(child.poll())

    def test_replacement_is_left_running_and_reported_before_credential_change(self):
        child=self.launch();send=signal.pidfd_send_signal;replacement=[]
        def replace(fd,sig):
            send(fd,sig);child.wait(timeout=2);self.path.unlink();replacement.append(self.launch())
        with patch.object(runtime.signal,'pidfd_send_signal',replace),self.assertRaises(runtime.StopError) as raised:
            runtime.stop(self.home,sys.executable,rpc_factory=Client,timeout=2)
        self.assertEqual(raised.exception.category,'runtime_replaced');self.assertIsNone(replacement[0].poll())

    def test_missing_kernel_handle_support_never_signals_numeric_pid(self):
        child=self.launch()
        with patch.object(runtime,'open_pidfd',side_effect=OSError('unsupported')),patch.object(runtime.signal,'pidfd_send_signal') as send,self.assertRaises(runtime.StopError):
            runtime.stop(self.home,sys.executable,rpc_factory=Client,timeout=.1)
        send.assert_not_called();self.assertIsNone(child.poll())


if __name__=='__main__':unittest.main()
