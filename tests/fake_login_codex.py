#!/usr/bin/env python3
"""Native stdio protocol fixture. Never connects to accounts or the network."""
import base64
import json
import os
from pathlib import Path
import sys
import time

for line in sys.stdin:
    message=json.loads(line);id=message.get('id');method=message.get('method')
    if id is None:continue
    if method=='initialize':result={}
    elif method=='account/login/start':
        result={'loginId':'fixture-login','verificationUrl':'https://auth.openai.com/codex/device','userCode':'TEST-1234'}
    elif method=='account/read':
        result={'account':{'type':'chatgpt','email':'fixture@example.com'},'workspaceRouting':{'chatgptAccountId':'fixture-account'}}
    else:raise AssertionError(method)
    print(json.dumps({'id':id,'result':result}),flush=True)
    if method=='account/login/start':
        identity={'email':'fixture@example.com','sub':'fixture-user'}
        token='x.'+base64.urlsafe_b64encode(json.dumps(identity).encode()).decode().rstrip('=')+'.x'
        auth={'tokens':{'account_id':'fixture-account','access_token':'fixture-access','refresh_token':'fixture-refresh','id_token':token}}
        path=Path(os.environ['CODEX_HOME'])/'auth.json';path.write_text(json.dumps(auth));path.chmod(0o600)
        print(json.dumps({'method':'account/login/completed','params':{'loginId':'fixture-login','success':True}}),flush=True)
