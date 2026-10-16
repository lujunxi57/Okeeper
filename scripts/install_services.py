#!/usr/bin/env python3
"""Render relocatable user services. Never starts/stops the main Codex daemon."""
import argparse
import ipaddress
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=18793)
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--start',action='store_true',help='Enable and start Okeeper services')
    parser.add_argument('--migrate-legacy',action='store_true',help='Disable the previous Rhythm/local-trigger services')
    args=parser.parse_args()
    ipaddress.ip_address(args.bind)
    if not 1<=args.port<=65535:parser.error('port must be between 1 and 65535')
    executable=Path(args.python).resolve()
    if not executable.is_file():parser.error('Python executable does not exist')
    # systemd expands percent specifiers even inside quoted strings.
    def quote(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    values={'@ROOT@':quote(ROOT),'@PYTHON@':quote(executable),'@BIND@':args.bind,'@PORT@':str(args.port)}
    destination=Path.home()/'.config/systemd/user';destination.mkdir(parents=True,exist_ok=True)
    def systemctl(*argv,check=True):return subprocess.run(['systemctl','--user',*argv],check=check)
    if args.migrate_legacy:
        legacy=['codex-rhythm-web.service','codex-rhythm-custom.timer','codex-rhythm-quota-watch.timer','codex-local-ok.timer','codex-local-ok-test.timer']
        existing=[name for name in legacy if (destination/name).exists()]
        if existing:systemctl('disable','--now',*existing)
    for source in sorted((ROOT/'deploy/systemd').iterdir()):
        content=source.read_text()
        for key,value in values.items():content=content.replace(key,value)
        # Quote the complete script path as one systemd argument.
        lines=[]
        for line in content.splitlines():
            if line.startswith('WorkingDirectory='):line='WorkingDirectory='+str(ROOT).replace('%','%%')
            if line.startswith('ExecStart='):
                name=source.name
                script={'okeeper-web.service':'web_server.py','okeeper-default.service':'trigger.py',
                        'okeeper-dispatch.service':'custom_dispatch.py','okeeper-quota-watch.service':'quota_watch.py'}[name]
                line='ExecStart='+quote(executable)+' '+quote(ROOT/'app'/script)+(' daily' if name=='okeeper-default.service' else '')
            lines.append(line)
        (destination/source.name).write_text('\n'.join(lines)+'\n')
    systemctl('daemon-reload')
    if args.start:systemctl('enable','--now','okeeper-web.service','okeeper-default.timer','okeeper-dispatch.timer','okeeper-quota-watch.timer')
    print('Installed Okeeper services from',ROOT)


if __name__=='__main__':main()
