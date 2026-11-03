"""Exercise the real Graph, feedback API and PostgreSQL relationships."""
from collections import Counter
import httpx
from sqlalchemy import select
from app.database import SessionLocal
from app.models import EvaluationCaseRecord as Case, EvaluationFeedbackRecord as Feedback
from scripts.verify_evaluation_case import _run_through_api


def main():
    base = 'http://127.0.0.1:18080'
    run, events = _run_through_api(base, '高级 AI 应用工程师')
    run_id = run['run_id']
    with SessionLocal() as db:
        cases = list(db.scalars(select(Case).where(Case.source_run_id == run_id)))
    counts = Counter(row.module_key for row in cases)
    assert set(counts) == {'request_understanding', 'job_matching', 'dimension_generation', 'fact_extraction'}, counts
    case = next(row for row in cases if row.module_key == 'request_understanding')
    with httpx.Client(base_url=base, timeout=30) as client:
        for scope, case_id, note in [('module', str(case.id), '课程验收：复核人才要求是否完整'), ('run', None, '课程验收：复核报告与证据的一致性')]:
            response = client.post('/api/evaluation/feedback', json={'source_run_id':run_id, 'case_id':case_id, 'feedback_scope':scope, 'note':note})
            response.raise_for_status()
        response = client.get('/api/evaluation/cases', params={'source_run_id':run_id, 'has_feedback':'true'})
        response.raise_for_status()
        assert [row['case_id'] for row in response.json()['items']] == [str(case.id)]
    with SessionLocal() as db:
        feedback = list(db.scalars(select(Feedback).where(Feedback.source_run_id == run_id)))
    assert len(feedback) == 2
    assert sum(row.case_id is not None for row in feedback) == 1
    print(f'run_id={run_id}')
    print(f'run_status={events[-1]}')
    for module, count in sorted(counts.items()):
        print(f'{module}={count}')
    print(f'failed_cases={sum(row.status == "failed" for row in cases)}')
    print(f'module_feedback_case_id={case.id}')
    print('feedback_rows=2')
    print('module_feedback=1 run_feedback=1')
    print('filtered_case_count=1')

if __name__ == '__main__':
    main()
