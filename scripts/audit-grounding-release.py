"""Save deployment health and synthetic-fixture audit without invoking any model."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import httpx
import paramiko

sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parents[1]
target = root / "docs/verification/grounding-20261119"
target.mkdir(parents=True, exist_ok=True)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(os.getenv("CENTOS_SSH_HOST", "192.168.163.128"), username="root",
               password=os.environ["CENTOS_SSH_PASSWORD"], timeout=10)


def remote(command, source=None):
    stdin, stdout, stderr = client.exec_command(command)
    if source is not None:
        stdin.write(source)
        stdin.flush()
    stdin.channel.shutdown_write()
    output, error = stdout.read().decode(), stderr.read().decode()
    if stdout.channel.recv_exit_status():
        raise RuntimeError(error or output)
    return output


try:
    data = json.loads(remote(
        "docker exec -i -w /app talent-eval-agents-course_backend_1 "
        "env PYTHONPATH=/app /app/.venv/bin/python -", """
import json
from collections import Counter
from pathlib import Path
from sqlalchemy import select
from app.database import SessionLocal
from app.models import EvaluationCaseRecord as Case, EvaluationAnnotationRecord as Annotation, TalentRunRecord as Run
from scripts.benchmark_assessment import load_cases, export_confirmed
manifest, fixtures = load_cases()
with SessionLocal() as db:
    cases = db.scalars(select(Case).where(Case.source_run_id.in_([
        'synthetic-assessment-v1-dev', 'synthetic-assessment-v1-test']))).all()
    assert len(cases) == 60
    by_id = {(row.replay_context or {})['case_id']: row for row in cases}
    pending = 0
    for fixture in fixtures:
        case = by_id[fixture['case_id']]
        annotation = db.get(Annotation, case.id)
        assert case.input_payload == fixture['input_payload']
        assert case.version_snapshot['dataset_hash'] == manifest['cases_sha256']
        if annotation.review_status != 'confirmed':
            assert annotation.golden_candidate == fixture['proposed_output']
            pending += 1
    runs = []
    for key in ['41a83f8d-3e86-4fe7-bf70-1a0322109fc4',
                '3b4e14b7-22f5-4f1f-ae27-7a936bdf7137',
                'f19c51f0-7aa1-45ec-920d-656d52fca4ec',
                'grounding-ui-fixture-offline-v1']:
        row = db.get(Run, key)
        assert row is not None
        snapshot = row.assessment_grounding or {}
        runs.append(dict(run_id=key, status=row.status, turn_no=row.turn_no,
            conversation_id=row.conversation_id, report_chars=len(row.report or ''),
            citations=len(row.citations or []), schema_version=snapshot.get('schema_version'),
            grounding_status=snapshot.get('status'), summary=snapshot.get('summary'),
            review_verdicts=dict(Counter(r['verdict'] for r in snapshot.get('reviews', []))),
            billing_error='Arrearage' in (row.error_message or '')))
path = Path('/tmp/grounding-confirmed-final.json')
confirmed = export_confirmed(fixtures, path)
print(json.dumps(dict(dataset_version=manifest['dataset_version'], dataset_hash=manifest['cases_sha256'],
    imported_cases=len(cases), pending=pending, exported_confirmed=confirmed,
    confirmed_export=json.loads(path.read_text()), runs=runs), ensure_ascii=False))
"""))
    data["containers"] = [json.loads(line) for line in remote(
        "docker ps --filter name=talent-eval-agents-course --format '{{json .}}'").splitlines()]
    assert len(data["containers"]) == 9
    assert all(row["State"] == "running" for row in data["containers"])
    files = []
    for container, remote_path, local_path in [
        ("talent-eval-agents-course_backend_1", "/app/app/assessment_grounding.py", "backend/app/assessment_grounding.py"),
        ("talent-eval-agents-course_backend_1", "/app/app/model_provider.py", "backend/app/model_provider.py"),
        ("talent-eval-agents-course_frontend_1", "/usr/share/nginx/html/index.html", "frontend/dist/index.html"),
    ]:
        remote_hash = remote(f"docker exec {container} sha256sum {remote_path}").split()[0]
        local_hash = hashlib.sha256((root / local_path).read_bytes()).hexdigest()
        assert remote_hash == local_hash, local_path
        files.append(dict(path=local_path, sha256=local_hash, deployed_match=True))
    data["files"] = files
    data["http"] = []
    for url in ["http://127.0.0.1:15173/", "http://127.0.0.1:18080/api/health"]:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        data["http"].append(dict(url=url, status=response.status_code))
    confirmed = data.pop("confirmed_export")
    (target / "confirmed-export.json").write_text(json.dumps(confirmed, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    data["checked_at"] = datetime.now(timezone.utc).isoformat()
    (target / "deployment-audit.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(dict(containers=len(data["containers"]), imported=data["imported_cases"],
        pending=data["pending"], confirmed=data["exported_confirmed"], http=data["http"],
        dataset_hash=data["dataset_hash"]), ensure_ascii=False))
finally:
    client.close()
