"""Fan out account alarms as independent, non-restarting lightweight jobs."""
import hashlib
from pathlib import Path
import subprocess
from settings import worker_environment
import sys
import account_store as store
from schedule_store import now

from settings import STATE


def launch(slot):
    unit='okeeper-account-'+hashlib.sha256(slot.encode()).hexdigest()[:24]
    subprocess.run(['systemd-run','--user','--quiet','--collect','--unit='+unit,
                    '--property=Type=exec','--property=Restart=no','--property=RuntimeMaxSec=300',
                    '--property=TimeoutStartSec=infinity','--property=UMask=0077',*worker_environment(),sys.executable,
                    str(Path(__file__).with_name('account_worker.py')),slot],check=True,timeout=10)


def main():
    current=now();scheduled=current.replace(second=0,microsecond=0)
    for alarm in store.due_alarms(STATE,current):
        slot=store.reserve(STATE,alarm,scheduled)
        if not slot:continue
        try:launch(slot)
        except Exception:
            run=store.begin(STATE,slot)
            if run:store.finish(STATE,run,'failed_or_uncertain',error='无法启动账号触发进程')


if __name__=='__main__':main()
