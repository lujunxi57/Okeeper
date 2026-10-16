"""Shared local paths. Keep the existing state directory across source moves."""
import os
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
STATE=Path(os.environ.get('OKEEPER_STATE_DIR',os.environ.get('RHYTHM_STATE_DIR',str(Path.home()/'.local/state/codex-local-trigger'))))
CODEX_HOME=Path(os.environ.get('OKEEPER_CODEX_HOME',str(Path.home()/'.codex')))
CODEX_BIN=os.environ.get('OKEEPER_CODEX_BIN',shutil.which('codex') or str(Path.home()/'.local/bin/codex'))
CODEX_SOCKET=os.environ.get('OKEEPER_CODEX_SOCKET',str(CODEX_HOME/'app-server-control/app-server-control.sock'))
WORKDIR=Path(os.environ.get('OKEEPER_WORKDIR',str(Path.home())))


def worker_environment():
    """Pass only application settings to transient user services; never tokens."""
    values={'OKEEPER_STATE_DIR':str(STATE),'OKEEPER_CODEX_HOME':str(CODEX_HOME),
            'OKEEPER_CODEX_BIN':CODEX_BIN,'OKEEPER_CODEX_SOCKET':CODEX_SOCKET,
            'OKEEPER_WORKDIR':str(WORKDIR)}
    if os.environ.get('TMPDIR'):values['TMPDIR']=os.environ['TMPDIR']
    return ['--setenv='+key+'='+value for key,value in values.items()]
