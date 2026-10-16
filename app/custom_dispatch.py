"""Minute dispatcher. Separate user services keep long tasks independent."""
import hashlib
import subprocess
from settings import worker_environment
import sys
import custom_store as store
from custom_worker import STATE,log
from schedule_store import now

def launch(slot):
    unit='okeeper-task-'+hashlib.sha256(slot.encode()).hexdigest()[:24]
    subprocess.run(['systemd-run','--user','--quiet','--collect','--unit='+unit,
        '--property=Type=exec','--property=Restart=no','--property=RuntimeMaxSec=infinity',
        '--property=TimeoutStartSec=infinity','--property=UMask=0077',*worker_environment(),sys.executable,
        str(__import__('pathlib').Path(__file__).with_name('custom_worker.py')),slot],check=True,timeout=10)

def main():
    # Fixed account alarms have their own worker and durable claims.
    from account_dispatch import main as dispatch_accounts
    dispatch_accounts()
    current=now();scheduled=current.replace(second=0,microsecond=0)
    for alarm in store.due_alarms(STATE,current):
        slot=store.reserve(STATE,alarm,scheduled)
        if not slot:continue
        try:
            launch(slot);log('custom_worker_launched',slot=slot,alarm_id=alarm['id'],thread=alarm['thread_id'])
        except Exception as error:
            run=store.begin(STATE,slot)
            if run:store.finish(STATE,run,'failed_or_uncertain',error='无法启动执行进程：'+str(error))
            log('custom_launch_failed',slot=slot,error=str(error))

if __name__=='__main__':main()
