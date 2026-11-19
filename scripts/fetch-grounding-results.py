"""Fetch only benchmark artifacts from the development container, never its environment."""
import os
from pathlib import Path
import sys
import paramiko

sys.stdout.reconfigure(encoding='utf-8')
root = Path(__file__).resolve().parents[1]
target = root / 'backend/samples/assessment-grounding-v1/results'
target.mkdir(parents=True, exist_ok=True)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(os.getenv('CENTOS_SSH_HOST', '192.168.163.128'), username='root', password=os.environ['CENTOS_SSH_PASSWORD'], timeout=10)
suffix = sys.argv[1] if len(sys.argv) > 1 else ''
for name in ('live-baseline', 'live-current', 'offline', 'outage'):
    source = '/tmp/talent-grounding-check/samples/assessment-grounding-v1/results/'+name+'.json'
    remote = '/tmp/grounding-result-'+name+'.json'
    _, out, err = client.exec_command(f'docker cp talent-eval-agents-course_backend_1:{source} {remote}')
    if out.channel.recv_exit_status() == 0:
        with client.open_sftp() as sftp:
            sftp.get(remote, str(target / (name+suffix+'.json')))
        print(name+suffix+'.json')
client.close()
