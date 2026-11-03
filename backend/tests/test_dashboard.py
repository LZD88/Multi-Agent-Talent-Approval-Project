from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import dashboard_metrics
from app.models import (
    Base,
    Document,
    EmployeeProfile,
    EvaluationCaseRecord,
    EvaluationDatasetItemRecord,
    TalentConversationRecord,
    TalentRunRecord,
)


def test_dashboard_metrics_count_core_business_records():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        employee = EmployeeProfile(employee_no='E001', name='测试员工')
        db.add(employee)
        db.flush()
        db.add(Document(candidate_id='E001', employee_id=employee.id, tenant_id='course-demo', title='简历', document_type='resume'))
        db.add(TalentConversationRecord(conversation_id='c1', title='会话', status='completed', latest_run_id='r1', created_at=now, updated_at=now))
        db.add(TalentRunRecord(run_id='r1', conversation_id='c1', turn_no=1, message='评估', status='completed', started_at=now, completed_at=now, updated_at=now))
        case = EvaluationCaseRecord(source_run_id='r1', conversation_id='c1', turn_no=1, module_key='request_understanding', invocation_no=1, input_payload={}, output_payload={})
        db.add(case)
        db.flush()
        db.add(EvaluationDatasetItemRecord(source_case_id=case.id, module_key='request_understanding', input_payload={}, golden_output={}, replay_context={}, dataset_split='test'))
        db.commit()

        assert dashboard_metrics(db) == {
            'employee_count': 1,
            'document_count': 1,
            'conversation_count': 1,
            'run_count': 1,
            'case_count': 1,
            'dataset_count': 1,
        }

    engine.dispose()
