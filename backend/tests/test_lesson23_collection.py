from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import delete
from app.database import SessionLocal
from app.models import EvaluationCaseRecord
from app import evaluation_case as ec
from app.talent_decision_graph import DecisionContext


def test_parallel_invocations_are_unique():
    run_id = str(uuid4())
    draft = ec.EvaluationCaseDraft(source_run_id=run_id, conversation_id=run_id, turn_no=1,
        module_key=ec.EvaluationModule.FACT_EXTRACTION, input_payload={}, output_payload={})
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _: ec.SqlEvaluationCaseStore(SessionLocal).record(draft), range(16)))
        assert sorted(row.invocation_no for row in rows) == list(range(1, 17))
        assert len({row.id for row in rows}) == 16
    finally:
        with SessionLocal() as db:
            db.execute(delete(EvaluationCaseRecord).where(EvaluationCaseRecord.source_run_id == run_id))
            db.commit()


def test_capture_failure_preserves_business_error_and_input():
    records = []
    context = DecisionContext(tenant_id='course-demo', permission_scopes=('hr_private',), run_id='r')
    def fail():
        raise ValueError('business')
    with pytest.raises(ValueError, match='business'):
        ec.capture_call(fail, context=context, module_key=ec.EvaluationModule.FACT_EXTRACTION,
            input_payload={'text': 'frozen'}, recorder=records.append)
    assert records[0].status == 'failed'
    assert records[0].input_payload == {'text': 'frozen'}
    def broken_recorder(draft):
        raise RuntimeError('storage')
    assert ec.capture_call(lambda: {'ok': True}, context=context,
        module_key=ec.EvaluationModule.FACT_EXTRACTION, input_payload={}, recorder=broken_recorder) == {'ok': True}


def test_tool_worker_preserves_extraction_identity():
    from app.talent_tools import ToolExecutor, TalentToolContext
    context = DecisionContext(tenant_id='course-demo', permission_scopes=('hr_private',), run_id='real-run')
    tool_context = TalentToolContext(tenant_id='course-demo', permission_scopes=('hr_private',), actor_id='test', run_id='task')
    with ec.extraction_scope(context, candidate_id='C001', requirement_id='1:1'):
        result = ToolExecutor().execute('search_candidate_evidence',
            lambda: {'identity': ec.current_extraction_scope()[1]}, context=tool_context, arguments={})
    assert result['ok']
    assert result['data']['identity']['candidate_id'] == 'C001'
    assert ec.current_extraction_scope() is None
