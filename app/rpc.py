import json, subprocess, selectors, time
from settings import CODEX_BIN as BIN,CODEX_SOCKET as SOCK
class RPC:
 def __init__(self,socket=None):
  from websockets.sync.client import unix_connect
  self.ws=unix_connect(socket or SOCK, uri='ws://localhost/', open_timeout=10, legacy=True)
  self.seq=0;self.events=[]
  self.call('initialize',{'clientInfo':{'name':'okeeper','title':'Okeeper','version':'1.0'},'capabilities':{'experimentalApi':True}})
  self.send({'method':'initialized'})
 def send(self,msg):
  self.ws.send(json.dumps(msg))
 def recv(self,timeout=10):
  return json.loads(self.ws.recv(timeout=timeout))
 def call(self,method,params):
  self.seq+=1;i=self.seq;self.send({'id':i,'method':method,'params':params});end=time.monotonic()+15
  while True:
   msg=self.recv(max(0,end-time.monotonic()))
   if msg.get('id')==i:
    if 'error' in msg:raise RuntimeError(json.dumps(msg['error']))
    return msg['result']
   self.events.append(msg)
 def close(self):
  self.ws.close()
