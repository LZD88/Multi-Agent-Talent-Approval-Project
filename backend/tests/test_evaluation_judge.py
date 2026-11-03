from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

GOLDEN = {'draft': {'input_mode': 'job_name', 'job_query': 'AI工程师'}, 'revision_action': 'initial', 'revision_summary': '建立初始人才要求', 'conversation_title': 'AI岗位'}

def test_chat_factory_uses_selected_model():
    from app.model_provider import get_chat_model
    from app.config import Settings
    assert get_chat_model(settings=Settings(_env_file=None, dashscope_api_key='test', chat_model='qwen-plus')).model_name == 'qwen-plus'

def test_failed_case_does_not_call_judge(monkeypatch):
    from app import evaluation_judge as j
    monkeypatch.setattr(j, 'get_judge_model', lambda **kw: pytest.fail('failed Case must not call model'))
    result = j.judge_case(SimpleNamespace(status='failed', error_code='TimeoutError'))
    assert result.score == 0 and result.golden_candidate is None

def test_judge_uses_separate_factory_and_minimal_payload(monkeypatch):
    from app import evaluation_judge as j
    captured = []
    schemas = []
    class Model:
        def with_structured_output(self, schema, **kw):
            schemas.append(schema)
            return self
        def invoke(self, messages):
            captured.extend(messages)
            return {'score': 2, 'error_types': ['omission'], 'reason': '遗漏', 'golden_candidate': GOLDEN, 'confidence': .8}
    monkeypatch.setattr(j, 'get_judge_model', lambda **kw: Model())
    case = SimpleNamespace(status='succeeded', module_key='request_understanding', input_payload={'messages': []}, output_payload=GOLDEN)
    assert j.judge_case(case).score == 2
    payload = __import__('json').loads(captured[1][1])
    assert set(payload) == {'module_key', 'task_spec', 'input', 'actual_output', 'generate_golden_candidate'}
    assert 'output_schema' not in payload
    assert 'previous_request' in payload['task_spec']
    assert schemas == [j.JudgeResponse]
    monkeypatch.setattr(j, 'get_judge_model', lambda **kw: None)
    with pytest.raises(RuntimeError, match='JUDGE'): j.judge_case(case)


def test_judge_redacts_pii_before_external_model_call(monkeypatch):
    from app import evaluation_judge as j

    captured = []

    class Model:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            captured.extend(messages)
            return {
                'score': 4,
                'error_types': [],
                'reason': '候选人 __PII_PERSON_001__ 的结果完整',
                'golden_candidate': None,
                'confidence': 0.9,
            }

    monkeypatch.setattr(j, 'get_judge_model', lambda **kwargs: Model())
    case = SimpleNamespace(
        status='succeeded',
        module_key='request_understanding',
        input_payload={'messages': [{
            'role': 'human',
                'content': '候选人姓名：张伟，手机 13812345678，邮箱 zhangwei@example.com',
        }]},
        output_payload=GOLDEN,
    )

    result = j.judge_case(case)

    outbound = captured[1][1]
    assert '张伟' not in outbound
    assert '13812345678' not in outbound
    assert 'zhangwei@example.com' not in outbound
    assert '__PII_PERSON_001__' in outbound
    assert result.reason == '候选人 __PII_PERSON_001__ 的结果完整'


def test_every_judge_module_has_a_task_spec():
    from app import evaluation_judge as j
    assert set(j.TASK_SPECS) == {
        'request_understanding', 'job_matching', 'dimension_generation', 'fact_extraction',
    }
    assert j.TASK_SPECS['request_understanding'].startswith('你是人才评估任务编译器')
    assert j.TASK_SPECS['dimension_generation'] == j.DIMENSION_GENERATOR_PROMPT
    assert j.TASK_SPECS['fact_extraction'] == j.EXTRACTION_PROMPT
    assert all(value.strip() for value in j.TASK_SPECS.values())


def test_invalid_actual_output_is_scored_without_calling_judge(monkeypatch):
    from app import evaluation_judge as j
    monkeypatch.setattr(j, 'get_judge_model', lambda **kw: pytest.fail('invalid output must be handled before Judge'))
    case = SimpleNamespace(status='succeeded', module_key='request_understanding', input_payload={'messages': []}, output_payload={})
    result = j.judge_case(case)
    assert result.score == 0
    assert result.error_types == ['output_schema_invalid']
    assert result.golden_candidate is None


def test_invalid_golden_candidate_keeps_the_judge_score(monkeypatch):
    from app import evaluation_judge as j
    class Model:
        def with_structured_output(self, schema, **kw): return self
        def invoke(self, messages):
            return j.JudgeResult(score=2, verdict='needs_review', error_types=['omission'], reason='存在遗漏', golden_candidate={}, confidence=.8)
    monkeypatch.setattr(j, 'get_judge_model', lambda **kw: Model())
    case = SimpleNamespace(status='succeeded', module_key='request_understanding', input_payload={'messages': []}, output_payload=GOLDEN)
    result = j.judge_case(case)
    assert result.score == 2
    assert result.golden_candidate is None
    assert result.error_types == ['omission', 'golden_candidate_invalid']

@pytest.fixture
def client(monkeypatch):
    from app import evaluation_api
    from app.models import Base, EvaluationCaseRecord
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(evaluation_api, 'SessionLocal', factory)
    with factory() as db:
        row = EvaluationCaseRecord(source_run_id='lesson24-test', conversation_id='c', turn_no=1, module_key='request_understanding', invocation_no=1, input_payload={'messages':[{'role':'human','content':'AI工程师'}]}, output_payload=GOLDEN)
        db.add(row); db.commit(); cid = str(row.id)
    app = FastAPI(); app.include_router(evaluation_api.router)
    yield TestClient(app), cid
    engine.dispose()

def test_review_freezes_dataset_and_preserves_case(client):
    c, cid = client
    assert c.get('/api/evaluation/dataset').json()['items'] == []
    assert c.post(f'/api/evaluation/cases/{cid}/confirm', json={'golden_output':{}}).status_code == 422
    response = c.post(f'/api/evaluation/cases/{cid}/confirm', json={'golden_output':GOLDEN, 'dataset_split':'test', 'tags':['critical']})
    assert response.status_code == 200, response.text
    assert c.post(f'/api/evaluation/cases/{cid}/confirm', json={'golden_output':GOLDEN}).status_code == 409
    detail = c.get(f'/api/evaluation/cases/{cid}').json()
    assert detail['output_payload'] == GOLDEN
    assert detail['annotation']['annotator_type'] == 'human'
    items = c.get('/api/evaluation/dataset').json()['items']
    assert len(items) == 1 and items[0]['id'] == response.json()['id']

def test_batch_isolates_errors_and_filters(client, monkeypatch):
    from app import evaluation_api as api
    from app.evaluation_judge import JudgeResult
    c, cid = client
    monkeypatch.setattr(api, 'judge_case', lambda case: JudgeResult(score=1, verdict='needs_review', error_types=['omission'], reason='遗漏', golden_candidate=GOLDEN, confidence=.7))
    result = c.post('/api/evaluation/judge', json={'case_ids':[cid, str(uuid4())]}).json()
    assert [r['status'] for r in result['items']] == ['annotated','error']
    assert len(c.get('/api/evaluation/cases?max_score=2').json()['items']) == 1
    assert c.get('/api/evaluation/cases?unannotated=true').json()['items'] == []
    assert c.post('/api/evaluation/judge', json={'case_ids':[cid]}).json()['items'][0]['status'] == 'skipped'
    c.post(f'/api/evaluation/cases/{cid}/confirm', json={'golden_output':GOLDEN})
    assert c.post('/api/evaluation/judge', json={'case_ids':[cid]}).json()['items'][0]['status'] == 'skipped'

def test_judge_outage_leaves_case_unannotated(client, monkeypatch):
    from app import evaluation_api as api
    c, cid = client
    def fail(case): raise RuntimeError('provider unavailable')
    monkeypatch.setattr(api, 'judge_case', fail)
    assert c.post('/api/evaluation/judge', json={'case_ids':[cid]}).json()['items'][0]['status'] == 'error'
    assert len(c.get('/api/evaluation/cases?unannotated=true').json()['items']) == 1


def test_invalid_judge_response_reports_the_validation_stage(client, monkeypatch):
    from app import evaluation_api as api
    from app.evaluation_judge import JudgeResponse
    c, cid = client
    def fail(case):
        return JudgeResponse.model_validate({})
    monkeypatch.setattr(api, 'judge_case', fail)
    item = c.post('/api/evaluation/judge', json={'case_ids':[cid]}).json()['items'][0]
    assert item['status'] == 'error'
    assert item['error_type'] == 'ValidationError'
    assert item['error_stage'] == 'judge_response'

def test_replay_uses_frozen_inputs(monkeypatch):
    from app import evaluation_judge as j
    calls = []
    def interpreter(messages, previous_request, selected_job=None, conversation_title=''):
        calls.append((messages, previous_request, selected_job, conversation_title))
        return j.output_schema('request_understanding').model_validate(GOLDEN)
    monkeypatch.setattr(j, 'model_request_interpreter', interpreter)
    item = SimpleNamespace(module_key='request_understanding', input_payload={'messages':[{'role':'human','content':'AI工程师'}], 'previous_request':{'x':1}, 'conversation_title':'旧标题', 'selected_job':{'name':'AI'}}, golden_output=GOLDEN)
    assert j.replay_output(item)['draft']['job_query'] == 'AI工程师'
    assert calls[0][0][0].content == 'AI工程师'
    assert calls[0][1:] == ({'x':1}, {'name':'AI'}, '旧标题')


def test_replay_supports_every_collected_module(monkeypatch):
    from app import evaluation_judge as j

    job_matches = [{
        'job_code': 'AI-01', 'name': 'AI工程师', 'content': '负责 AI 应用开发',
        'version': 1, 'match_type': 'exact', 'match_score': 1.0,
    }]
    monkeypatch.setattr(j, 'database_job_lookup', lambda query, context: job_matches)

    class DimensionGenerator:
        def __call__(self, talent_request, query_plan):
            return j.EvaluationDimensionPlan(dimensions=[{
                'name': '项目经验', 'definition': '核验项目经验', 'weight_percent': 100,
                'evidence_requirements': ['存在项目证据'],
                'score_anchors': [
                    {'score': 0, 'description': '无证据'},
                    {'score': 3, 'description': '部分证据'},
                    {'score': 5, 'description': '充分证据'},
                ],
                'retrieval_hints': ['项目'], 'source_requirement_ids': ['R1'],
            }])

    monkeypatch.setattr(j, 'build_structured_dimension_generator', lambda provider: DimensionGenerator())

    class StructuredModel:
        def with_structured_output(self, schema, **kwargs): return self
        def invoke(self, messages):
            return {'facts': [], 'fully_supported': False, 'missing_information': ['材料不足']}

    monkeypatch.setattr(j, 'get_chat_model', lambda **kwargs: StructuredModel())

    job = SimpleNamespace(module_key='job_matching', input_payload={'job_query': 'AI工程师'},
                          replay_context={'tenant_id': 'course-demo', 'permission_scopes': ['hr_private']})
    dimensions = SimpleNamespace(module_key='dimension_generation', input_payload={'talent_request': {}, 'query_plan': {}}, replay_context={})
    facts = SimpleNamespace(module_key='fact_extraction', input_payload={
        'requirement': '有 Agent 评测经验',
        'sources': [{'chunk_id': 'source_1', 'content': '负责 Agent 评测'}],
        'output_schema': j.EvidenceExtraction.model_json_schema(),
    }, replay_context={})

    assert j.replay_output(job)['route'] == 'select_exact_job'
    assert j.replay_output(dimensions)['dimensions'][0]['name'] == '项目经验'
    assert j.replay_output(facts)['missing_information'] == ['材料不足']


def test_replay_rejects_unknown_module():
    from app import evaluation_judge as j
    with pytest.raises(ValueError, match='不支持的评测模块'):
        j.replay_output(SimpleNamespace(module_key='unknown', input_payload={}, replay_context={}))


def test_replay_judge_failure_does_not_become_zero_score(client, monkeypatch):
    from app import evaluation_api as api
    c, cid = client
    item = c.post(f'/api/evaluation/cases/{cid}/confirm', json={'golden_output':GOLDEN}).json()
    monkeypatch.setattr(api, 'replay_output', lambda item: GOLDEN)
    def fail(case, **kwargs): raise RuntimeError('judge outage')
    monkeypatch.setattr(api, 'judge_case', fail)
    result = c.post('/api/evaluation/replay', json={'dataset_item_ids':[item['id']]}).json()
    assert result['complete'] is False
    assert result['average_score'] is None
    assert result['items'][0]['status'] == 'judge_error'


def test_replay_module_failure_counts_as_zero(client, monkeypatch):
    from app import evaluation_api as api
    c, cid = client
    item = c.post(f'/api/evaluation/cases/{cid}/confirm', json={'golden_output':GOLDEN}).json()
    def fail(item): raise ValueError('invalid module output')
    monkeypatch.setattr(api, 'replay_output', fail)
    result = c.post('/api/evaluation/replay', json={'dataset_item_ids':[item['id']]}).json()
    assert result['average_score'] == 0
    assert result['items'][0]['status'] == 'module_failed'


def test_run_groups_sort_by_completion_and_count_all_matching_cases(client):
    from app import evaluation_api as api
    from app.models import TalentRunRecord, TalentConversationRecord, EvaluationCaseRecord
    from datetime import datetime, timezone, timedelta
    c, _ = client
    now = datetime.now(timezone.utc)
    with api.SessionLocal() as db:
        db.add(TalentConversationRecord(conversation_id='group-conv', title='上海工程师', status='active', created_at=now, updated_at=now))
        for index in range(11):
            rid = f'group-{index}'
            db.add(TalentRunRecord(run_id=rid, conversation_id='group-conv', turn_no=index+1, message='test', status='completed', started_at=now, updated_at=now, completed_at=now+timedelta(minutes=index)))
            for invocation in range(1, 13):
                db.add(EvaluationCaseRecord(source_run_id=rid, conversation_id='group-conv', turn_no=index+1, module_key='job_matching', invocation_no=invocation, input_payload={}, output_payload={}))
        db.commit()
    first = c.get('/api/evaluation/cases?group_by=run&module_key=job_matching&limit=10').json()
    assert len(first['items']) == 10 and first['has_more']
    assert first['items'][0]['source_run_id'] == 'group-10'
    assert first['items'][0]['case_count'] == 12
    assert first['items'][0]['conversation_title'] == '上海工程师'
    second = c.get('/api/evaluation/cases?group_by=run&module_key=job_matching&limit=10&offset=10').json()
    assert [r['source_run_id'] for r in second['items']] == ['group-0']
    detail = c.get('/api/evaluation/cases?source_run_id=group-10&limit=10').json()
    assert len(detail['items']) == 10 and detail['has_more']
