"""Run current backend tests in an isolated directory of the existing dev container."""
import io
import os
from pathlib import Path
import shlex
import sys
import tarfile

import paramiko

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

root = Path(__file__).resolve().parents[1]
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
    for name in ("app", "tests", "scripts", "samples", "utils"):
        for path in (root / "backend" / name).rglob("*"):
            if path.is_file() and not any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
                bundle.add(path, arcname=str(path.relative_to(root / "backend")))
    bundle.add(root / "backend" / "pyproject.toml", arcname="pyproject.toml")
    bundle.add(root / "backend" / "langgraph.json", arcname="langgraph.json")
    bundle.add(root / "database" / "migrations" / "025_assessment_grounding.sql",
               arcname="database/migrations/025_assessment_grounding.sql")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(os.getenv("CENTOS_SSH_HOST", "192.168.163.128"), username="root",
               password=os.environ["CENTOS_SSH_PASSWORD"], timeout=10)
with client.open_sftp() as sftp:
    with sftp.file("/tmp/talent-grounding-check.tar.gz", "wb") as target:
        target.write(archive.getvalue())
container = "talent-eval-agents-course_backend_1"
command = (f"docker exec {container} mkdir -p /tmp/talent-grounding-check && "
           f"docker cp /tmp/talent-grounding-check.tar.gz {container}:/tmp/talent-grounding-check.tar.gz && "
           f"docker exec {container} tar xzf /tmp/talent-grounding-check.tar.gz -C /tmp/talent-grounding-check && "
           f"docker exec -w /tmp/talent-grounding-check {container} env -u MILVUS_URI "
           "PYTHONPATH=/tmp/talent-grounding-check /app/.venv/bin/python " +
           " ".join(shlex.quote(arg) for arg in (sys.argv[1:] or ["-m", "pytest", "-q"])))
_, out, err = client.exec_command(command)
for line in out:
    sys.stdout.write(line)
sys.stderr.write(err.read().decode())
status = out.channel.recv_exit_status()
client.close()
raise SystemExit(status)
