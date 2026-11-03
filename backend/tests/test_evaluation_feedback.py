from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_feedback_rejects_missing_case_and_invalid_scope():
    from app.evaluation_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.post('/api/evaluation/feedback', json={
        'source_run_id': 'unknown', 'feedback_scope': 'module', 'note': ''}).status_code == 422
    assert client.post('/api/evaluation/feedback', json={
        'source_run_id': 'unknown', 'feedback_scope': 'run', 'case_id': 'bad'}).status_code == 422
    assert client.post('/api/evaluation/feedback', json={
        'source_run_id': 'unknown', 'feedback_scope': 'run'}).status_code == 404


def test_feedback_roundtrip_preserves_cases_and_does_not_fan_out(monkeypatch):
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app import evaluation_api
    from app.models import Base, TalentRunRecord, EvaluationCaseRecord
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(evaluation_api, 'SessionLocal', factory)
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(TalentRunRecord(run_id='r', conversation_id='c', turn_no=1, message='test', status='completed', started_at=now, updated_at=now))
        case = EvaluationCaseRecord(source_run_id='r', conversation_id='c', turn_no=1, module_key='request_understanding', invocation_no=1, input_payload={'text':'frozen'}, output_payload={'actual':True})
        db.add(case); db.commit(); db.refresh(case); case_id = str(case.id)
    app = FastAPI(); app.include_router(evaluation_api.router); client = TestClient(app)
    for scope, selected in [('run', None), ('module', case_id)]:
        response = client.post('/api/evaluation/feedback', json={'source_run_id':'r', 'case_id':selected, 'feedback_scope':scope})
        assert response.status_code == 201
    rows = client.get('/api/evaluation/cases?has_feedback=true&module_key=request_understanding').json()['items']
    assert len(rows) == 1 and rows[0]['has_feedback'] and rows[0]['run_has_feedback']
    detail = client.get(f'/api/evaluation/cases/{case_id}').json()
    assert detail['input_payload'] == {'text':'frozen'}
    assert detail['annotation_status'] == 'unlabeled'
    assert {r['feedback_scope'] for r in detail['feedback']} == {'module','run'}
    assert client.post('/api/evaluation/feedback', json={'source_run_id':'other', 'case_id':case_id, 'feedback_scope':'module'}).status_code == 404
