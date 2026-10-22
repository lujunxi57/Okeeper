#!/usr/bin/env python3
"""Test-only native protocol peer. No network, real accounts or model calls."""
import base64
import json
import os
from pathlib import Path
import sys
import threading
import time


def auth(email='added@example.com'):
    payload = {'email': email, 'sub': email, 'https://api.openai.com/auth': {'chatgpt_plan_type': 'plus'}}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return {'auth_mode': 'chatgpt', 'tokens': {'id_token': 'test.' + encoded + '.signature',
        'access_token': 'fake-access', 'refresh_token': 'fake-refresh', 'account_id': 'workspace-' + email}}


if __name__ == '__main__':
    if '--version' in sys.argv:
        print('codex-cli fixture')
        raise SystemExit()
    home = Path(os.environ['CODEX_HOME'])
    signal = Path(os.environ['OKEEPER_TEST_LOGIN_SIGNAL'])
    output_lock = threading.Lock()

    def send(value):
        with output_lock:
            print(json.dumps(value), flush=True)

    def complete():
        while not signal.exists():
            time.sleep(.03)
        (home / 'auth.json').write_text(json.dumps(auth()))
        send({'method': 'account/login/completed', 'params': {'loginId': 'fixture-login', 'success': True}})

    for line in sys.stdin:
        request = json.loads(line)
        method = request.get('method')
        if method == 'initialize':
            send({'id': request['id'], 'result': {'userAgent': 'fixture'}})
        elif method == 'account/login/start':
            assert request['params'] == {'type': 'chatgptDeviceCode'}
            send({'id': request['id'], 'result': {'type': 'chatgptDeviceCode', 'loginId': 'fixture-login',
                'verificationUrl': 'https://auth.openai.com/codex/device', 'userCode': 'TEST-1234'}})
            threading.Thread(target=complete, daemon=True).start()
        elif method == 'account/read':
            send({'id': request['id'], 'result': {'account': {'type': 'chatgpt', 'email': 'added@example.com'}}})
