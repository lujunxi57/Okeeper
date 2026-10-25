"""Install the official native Codex package without a Node.js runtime."""
import base64
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import tarfile
import urllib.request


version = sys.argv[1]
architecture = {'x86_64': 'x64', 'aarch64': 'arm64'}[platform.machine()]
url = f'https://registry.npmjs.org/@openai/codex/{version}-linux-{architecture}'
with urllib.request.urlopen(url, timeout=60) as response:
    package = json.load(response)
with urllib.request.urlopen(package['dist']['tarball'], timeout=60) as response:
    archive = response.read()
integrity = 'sha512-' + base64.b64encode(hashlib.sha512(archive).digest()).decode()
if integrity != package['dist']['integrity']:
    raise SystemExit('Codex package integrity check failed')
destination = Path('/opt/codex')
destination.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
    source.extractall(destination, filter='data')
vendor = destination / 'package/vendor'
binary, = vendor.glob('*/bin/codex')
Path('/usr/local/bin/codex').symlink_to(binary)
for helper in vendor.glob('*/path/*'):
    Path('/usr/local/bin', helper.name).symlink_to(helper)
print(f'Installed official Codex {version} ({architecture})', flush=True)
