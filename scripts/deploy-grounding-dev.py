"""Deploy the verified checkout to the existing local development VM, preserving rollback artifacts."""
import io
import os
from pathlib import Path
import shlex
import sys
import tarfile
from datetime import datetime
import paramiko

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
root = Path(__file__).resolve().parents[1]
release = 'grounding-'+datetime.now().strftime('%Y%m%d-%H%M%S')
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
    for directory in ('backend/app', 'backend/tests', 'backend/scripts', 'backend/samples', 'backend/utils',
                      'frontend/src', 'frontend/dist', 'database/migrations'):
        for path in (root / directory).rglob('*'):
            if path.is_file() and not any(part in {'__pycache__', '.pytest_cache'} for part in path.parts):
                bundle.add(path, arcname=path.relative_to(root).as_posix())
    for file in ('backend/ASSESSMENT_GROUNDING.md', '.env.example'):
        bundle.add(root / file, arcname=file)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(os.getenv('CENTOS_SSH_HOST', '192.168.163.128'), username='root',
               password=os.environ['CENTOS_SSH_PASSWORD'], timeout=10)
remote = '/tmp/'+release
with client.open_sftp() as sftp:
    with sftp.file(remote+'.tar.gz', 'wb') as f:
        f.write(archive.getvalue())
base = '/root/talent-eval-agents'
backend = 'talent-eval-agents-course_backend_1'
worker = 'talent-eval-agents-course_worker_1'
frontend = 'talent-eval-agents-course_frontend_1'
image = 'talent-eval-agents-course-backend'
frontimage = 'talent-eval-agents-course_frontend'
command = f'''set -e
mkdir -p {remote} /root/talent-deploy-backups
tar czf /root/talent-deploy-backups/{release}-source.tar.gz -C {base} backend/app backend/scripts frontend/src frontend/dist database/migrations
docker tag {image}:latest {image}:{release}-before
docker tag {frontimage}:latest {frontimage}:{release}-before
tar xzf {remote}.tar.gz -C {remote}
cat > {remote}/backend.Dockerfile <<'EOF'
FROM {image}:{release}-before
COPY backend/app /app/app
COPY backend/scripts /app/scripts
COPY backend/samples /app/samples
EOF
cat > {remote}/frontend.Dockerfile <<'EOF'
FROM {frontimage}:{release}-before
COPY frontend/dist /usr/share/nginx/html
EOF
docker build -t {image}:latest -f {remote}/backend.Dockerfile {remote}
docker build -t {frontimage}:latest -f {remote}/frontend.Dockerfile {remote}
docker exec -i talent-eval-agents-course_postgres_1 psql -v ON_ERROR_STOP=1 -U talent -d talent_docs < {remote}/database/migrations/025_assessment_grounding.sql
tar xzf {remote}.tar.gz -C {base}
docker cp {remote}/backend/app/. {backend}:/app/app
docker cp {remote}/backend/scripts/. {backend}:/app/scripts
docker exec {backend} mkdir -p /app/samples
docker cp {remote}/backend/samples/. {backend}:/app/samples
docker cp {remote}/backend/app/. {worker}:/app/app
docker cp {remote}/frontend/dist/. {frontend}:/usr/share/nginx/html
docker restart {backend} {worker}
echo DEPLOYED_RELEASE={release}
'''
_, output, errors = client.exec_command(command)
for line in output:
    sys.stdout.write(line)
sys.stderr.write(errors.read().decode())
status = output.channel.recv_exit_status()
client.close()
raise SystemExit(status)
