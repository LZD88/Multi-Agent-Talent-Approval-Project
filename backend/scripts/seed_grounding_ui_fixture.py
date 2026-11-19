"""Explicitly fictional, injected-review fixture for empty-checklist UI acceptance."""
from datetime import datetime, timezone
from types import SimpleNamespace
from app.assessment_grounding import GroundingReview, ClaimReview
from app.talent_evaluation_report import DimensionJudgement, ReportDraft, CandidateReportDraft, ReportPoint, build_talent_evaluation_report_graph
from app.database import SessionLocal
from app.talent_run_history import SqlTalentRunHistoryStore

run_id = 'grounding-ui-fixture-offline-v1'
store = SqlTalentRunHistoryStore(SessionLocal)
if store.get_run(run_id):
    print(run_id+' already exists')
    raise SystemExit(0)
source = dict(document_id='synthetic-doc', document_version_id='synthetic-version', chunk_id='synthetic-chunk',
    quote='【虚构界面测试数据】2025年参与原型接口开发。', quote_start=0, quote_end=len('【虚构界面测试数据】2025年参与原型接口开发。'),
    citation_id='synthetic-chunk', source_label='离线注入测试，不是真实模型结果')
dimension = dict(name='虚构原型接口职责', definition='仅检查展示', weight_percent=100,
    score_anchors=[dict(score=s, description=f'虚构测试档位{s}') for s in (0,3,5)],
    evidence_requirements=['接口记录'], retrieval_hints=['原型'], source_requirement_ids=['S1'])
branch = dict(task_id='SYN-UI:1', candidate_id='SYN-UI', dimension_number=1, execution_status='succeeded', requirements=[
    dict(requirement_id='S1', query='接口职责', status='sufficient', reason='虚构', extraction_status='succeeded',
         facts=[dict(event='虚构原型', period='2025', claim='参与接口开发', answer='yes', sources=[source])])])
def injected(claims, anchors):
    return GroundingReview(reviews=[ClaimReview(claim_id=row.claim_id, verdict='supported', reason_code='offline_injected',
        reason='仅验证页面，审查结果为离线注入，不是模型质量结论。', anchor_ids=row.anchor_ids) for row in claims])
result = build_talent_evaluation_report_graph(claim_reviewer=injected,
    dimension_scorer=lambda d,b,items: DimensionJudgement(evidence_status='sufficient', score=3, confidence='high',
        evidence_refs=[items[0].ref_id], rationale='离线注入展示测试'),
    report_writer=lambda c,items: ReportDraft(title='虚构界面验收', overview='离线注入', candidates=[CandidateReportDraft(
        candidate_id='SYN-UI', summary='离线注入', highlights=[ReportPoint(text='虚构材料记载参与原型接口开发',evidence_refs=[items[0].ref_id])])])).invoke(
    {'dimensions':[dimension], 'branch_results':[branch]})
snapshot = result['assessment_grounding']
snapshot['model'] = 'offline-injected-fixture'
now = datetime.now(timezone.utc).isoformat()
def event(no, kind, data):
    store.record(SimpleNamespace(run_id=run_id, sequence=no, type=kind, timestamp=now, data=data))
event(1,'run.started',{'conversation_id':run_id,'turn_no':1,'message':'【虚构UI验收】离线注入结果，仅测试空清单；非真实模型结果'})
event(2,'conversation.updated',{'title':'【虚构UI验收】空清单（离线注入）'})
event(3,'report.completed',{'markdown':'# 【虚构UI验收 · 离线注入】\n\n仅用于验证空清单展示，不代表真实模型结果。\n\n'+result['report'],
    'assessment_grounding':snapshot,'citations':[]})
event(4,'run.completed',{'status':'completed'})
assert not snapshot['verification']['candidates']['SYN-UI']['items']
print(run_id+' created with empty checklist; explicitly offline injected')
