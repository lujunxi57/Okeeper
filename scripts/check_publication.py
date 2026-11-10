#!/usr/bin/env python3
"""Check staged source boundaries before uploading. Prints paths, never secrets."""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
paths=subprocess.check_output(['git','ls-files','--cached','-z'],cwd=ROOT).decode().split('\0')
forbidden={'node_modules','artifacts','state','backups','auth-profiles','account-homes','__pycache__'}
static_images={'web/icon.png'}
secrets=re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{25,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})')
errors=[]
for name in filter(None,paths):
    path=Path(name)
    if forbidden.intersection(path.parts) or path.suffix in {'.sqlite','.db','.pem','.key','.jsonl'} or (path.suffix=='.png' and name not in static_images) or path.name.startswith('.env'):
        errors.append(name+': runtime/private/generated file')
        continue
    # Inspect the exact staged blob, even if the working copy has since changed.
    mode=subprocess.check_output(['git','ls-files','--stage','--',name],cwd=ROOT).split()[0]
    if mode==b'120000':errors.append(name+': symlink requires manual boundary review');continue
    value=subprocess.check_output(['git','show',':'+name],cwd=ROOT)
    if len(value)>2_000_000:errors.append(name+': unexpected large source file')
    if name in static_images and not value.startswith(b'\x89PNG\r\n\x1a\n'):
        errors.append(name+': invalid static PNG asset')
    if secrets.search(value):errors.append(name+': possible credential')
    if ('/home/'+__import__('getpass').getuser()).encode() in value:errors.append(name+': machine-specific path/address')
if errors:
    print('\n'.join(errors));sys.exit(1)
print(f'Publication boundary passed: {len(list(filter(None,paths)))} staged source files; no runtime state or detected credentials.')
