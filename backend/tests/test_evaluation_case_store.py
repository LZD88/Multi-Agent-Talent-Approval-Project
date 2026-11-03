from uuid import uuid4

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.evaluation_case import EvaluationCaseDraft, EvaluationModule, SqlEvaluationCaseStore
from app.models import EvaluationCaseRecord


def test_store_persists_real_module_invocations_in_postgres():
    store = SqlEvaluationCaseStore(SessionLocal)
    source_run_id = f"test-{uuid4()}"
    draft = EvaluationCaseDraft(
        source_run_id=source_run_id,
        conversation_id=f"conversation-{uuid4()}",
        turn_no=1,
        module_key=EvaluationModule.REQUEST_UNDERSTANDING,
        input_payload={"messages": [{"role": "human", "content": "寻找 AI 工程师"}]},
        output_payload={"input_mode": "job_name", "job_query": "AI 工程师"},
        replay_context={"tenant_id": "course-demo"},
        version_snapshot={"prompt": "request-understanding-v1"},
        duration_ms=12,
    )

    try:
        first = store.record(draft)
        second = store.record(draft)

        with SessionLocal() as session:
            records = list(
                session.scalars(
                    select(EvaluationCaseRecord)
                    .where(EvaluationCaseRecord.source_run_id == source_run_id)
                    .order_by(EvaluationCaseRecord.invocation_no)
                )
            )
        assert first.invocation_no == 1
        assert second.invocation_no == 2
        assert [item.input_payload for item in records] == [draft.input_payload] * 2
        assert records[0].annotation_status == "unlabeled"
    finally:
        with SessionLocal() as session:
            session.execute(
                delete(EvaluationCaseRecord).where(
                    EvaluationCaseRecord.source_run_id == source_run_id
                )
            )
            session.commit()
