"""Isolated native Codex process for account authorization and renewal only."""
import json
import os
import queue
import subprocess
import threading
import time

from settings import CODEX_BIN


class NativeClient:
    def __init__(self,home):
        env={k:v for k,v in os.environ.items() if k not in {
            'CODEX_HOME','CODEX_SQLITE_HOME','CODEX_ACCESS_TOKEN','CODEX_API_KEY','OPENAI_API_KEY','OPENAI_BASE_URL'}}
        env['CODEX_HOME']=str(home)
        self.process=subprocess.Popen([CODEX_BIN,'app-server','--stdio','-c','cli_auth_credentials_store="file"',
            '-c','model_provider="openai"','-c','approval_policy="never"','-c','sandbox_mode="read-only"'],
            cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
        self.incoming=queue.Queue();self.events=[];self.seq=0;self.lock=threading.Lock()
        threading.Thread(target=self._read,daemon=True).start()
        try:
            self.call('initialize',{'clientInfo':{'name':'okeeper_accounts','title':'Okeeper accounts','version':'1.0'}})
            self.send({'method':'initialized'})
        except Exception:self.close();raise
    def _read(self):
        try:
            for line in self.process.stdout:
                try:self.incoming.put(json.loads(line))
                except ValueError:continue
        except (OSError,ValueError):pass
        finally:self.incoming.put(None)
    def send(self,message):
        self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()
    def receive(self,timeout):
        message=self.incoming.get(timeout=timeout)
        if message is None:raise ConnectionError('Codex 登录进程已退出')
        return message
    def call(self,method,params):
        self.seq+=1;seq=self.seq;self.send({'id':seq,'method':method,'params':params});deadline=time.monotonic()+30
        while True:
            message=self.receive(max(.01,deadline-time.monotonic()))
            if message.get('id')==seq:
                if 'error' in message:raise RuntimeError('Codex 账号请求未完成')
                return message.get('result',{})
            self.events.append(message)
    def close(self):
        with self.lock:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=4)
            for stream in (self.process.stdin,self.process.stdout):
                if stream and not stream.closed:stream.close()
