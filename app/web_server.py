#!/usr/bin/env python3
"""Private-network alarm UI; time settings are the only writable controls."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import re
import sqlite3
from urllib.parse import urlsplit
from schedule_store import create_alarm, delete_alarm, snapshot, update_alarm, connect, database, delivery_fields, account_fields, ALARM_FIELDS, advance_alarm
from session_catalog import list_sessions, validate_target
from account_quota import cache as quota_cache
from account_rpc import account_catalog,find_account
import account_control
import quota_watch

from settings import ROOT
from settings import STATE
STATIC = ROOT / 'web'

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass
    def reply(self, code, data, mime='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode() if mime.startswith('application/json') else data
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/quota':
            self.reply(200, quota_cache.get(force=urlsplit(self.path).query == 'refresh=1'))
            return
        if path == '/api/accounts':
            try:self.reply(200,{'accounts':account_catalog()})
            except Exception:self.reply(503,{'error':'暂时无法读取账号列表'})
            return
        if path == '/api/account-control':
            try:self.reply(200,account_control.public_state(STATE))
            except Exception:self.reply(503,{'error':'暂时无法读取主后台状态'})
            return
        if path == '/api/quota-watch':
            self.reply(200,quota_watch.public_state(STATE))
            return
        if path == '/api/sessions':
            try:self.reply(200,{'sessions':list_sessions(STATE)})
            except Exception:self.reply(503,{'error':'暂时无法读取服务器会话，请稍后重试'})
            return
        if path == '/api/alarms':
            try:
                self.reply(200, snapshot(STATE))
            except Exception:
                self.reply(503, {'error': '暂时无法读取时间设置，请稍后重试'})
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/vendor/daisyui-5.7.50.css': ('vendor/daisyui-5.7.50.css', 'text/css; charset=utf-8'),
                  '/vendor/motion-14.2.0.js': ('vendor/motion-14.2.0.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
        if path not in assets:
            self.reply(404, {'error': '页面不存在'}); return
        name, mime = assets[path]
        self.reply(200, (STATIC / name).read_bytes(), mime)
    def mutation(self):
        try:
            # Same-origin JSON requests only; no cross-site form mutations.
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                self.reply(403, {'error': '请求来源无效'}); return
            if self.headers.get('Sec-Fetch-Site') == 'cross-site' or self.headers.get('X-Rhythm-Request') != '1':
                self.reply(403, {'error': '请求来源无效'}); return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply(415, {'error': '请求格式无效'}); return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 196608:
                self.reply(413, {'error': '请求过大'}); return
            payload = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(payload,dict):raise ValueError('请求格式无效')
            path = urlsplit(self.path).path
            if self.command=='PATCH' and path=='/api/quota-watch':
                self.reply(200,quota_watch.configure(STATE,payload))
                return
            if self.command=='POST' and path=='/api/account-control':
                self.reply(202,account_control.request_operation(payload,STATE))
                return
            advance=re.fullmatch(r'/api/alarms/([a-f0-9-]{36})/advance',path)
            if self.command=='POST' and advance:
                if payload:raise ValueError('新增请求无效')
                alarm_id=advance_alarm(STATE,advance.group(1))
                self.reply(200,{**snapshot(STATE),'created_alarm_id':alarm_id})
                return
            match = re.fullmatch(r'/api/alarms/([a-f0-9-]{36})', path)
            thread_name=None
            account_name=None
            if self.command in ('POST','PATCH') and (path=='/api/alarms' or match):
                if set(payload)-ALARM_FIELDS:raise ValueError('不能修改模型、思考强度或其他执行设置')
                old={}
                if self.command=='PATCH' and match:
                    with database(STATE) as db:
                        row=db.execute('SELECT * FROM alarms WHERE id=?',(match.group(1),)).fetchone()
                        if row is None:raise LookupError('这个时间已被删除')
                        old=dict(row)
                target,thread,_,_=delivery_fields(payload,old)
                account,_=account_fields(payload,old)
                if account and (self.command=='POST' or set(payload)&{'account_id','target_type'}):
                    record=find_account(account)
                    if not record:raise ValueError('此账号已不存在，请重新选择')
                    account_name=record['label']
                if target=='thread' and (self.command=='POST' or set(payload)&{'target_type','thread_id','prompt'}):
                    try:_,meta=validate_target(STATE,thread)
                    except ValueError:raise
                    except Exception as error:raise ValueError('会话不存在或暂时无法访问，请重新选择') from error
                    thread_name=meta['name']
            if self.command == 'POST' and path == '/api/alarms':
                create_alarm(STATE, payload,thread_name=thread_name,account_name=account_name)
            elif self.command == 'PATCH' and match:
                update_alarm(STATE, match.group(1), payload,thread_name=thread_name,account_name=account_name)
            elif self.command == 'DELETE' and match:
                if payload != {}:
                    raise ValueError('删除请求无效')
                delete_alarm(STATE, match.group(1))
            else:
                self.reply(404, {'error': '操作不存在'}); return
            self.reply(200, snapshot(STATE))
        except BlockingIOError:
            self.reply(409,{'error':'已有账号切换或重启正在进行，请等待完成'})
        except sqlite3.IntegrityError:
            self.reply(409, {'error': '这个发送目标在该时间已经添加了'})
        except LookupError as e:
            self.reply(404, {'error': str(e)})
        except (ValueError, UnicodeError) as e:
            self.reply(400, {'error': str(e) if isinstance(e, ValueError) and not isinstance(e, json.JSONDecodeError) else '请求格式无效'})
        except Exception:
            self.reply(503, {'error': '保存失败，请稍后再试'})
    do_POST = mutation
    do_PATCH = mutation
    do_DELETE = mutation

def main():
    db = connect(STATE); db.close()
    host = os.environ.get('OKEEPER_BIND',os.environ.get('RHYTHM_BIND','127.0.0.1'))
    port = int(os.environ.get('OKEEPER_PORT',os.environ.get('RHYTHM_PORT','18793')))
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f'Okeeper listening on http://{host}:{port}', flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
