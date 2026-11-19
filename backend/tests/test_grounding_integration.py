"""Offline full parent/subgraph/SSE/history regression with explicit fictional data."""
import asyncio

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.assessment_grounding import ClaimReview, GroundingReview
from app.models import Base
from app.talent_decision_engine import build_talent_decision_engine
from app.talent_evaluation_report import (DimensionJudgement, ReportDraft, CandidateReportDraft,
    ReportPoint, build_talent_evaluation_report_graph)
from app.talent_run_stream import TalentRunManager, RunStatus
from app.talent_run_history import SqlTalentRunHistoryStore
from test_assessment_grounding import branch, dimension, supported


def one_node(update):
    graph = StateGraph(dict)
    graph.add_node('work', lambda _: update)
    graph.add_edge(START, 'work')
    graph.add_edge('work', END)
    return graph.compile()


def test_parent_sse_history_normal_missing_negative_conflict_outage_and_followup(monkeypatch):
    from app import talent_evaluation_report as module
    monkeypatch.setattr(module, 'capture_call', lambda call, **kw: call())
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    history = SqlTalentRunHistoryStore(sessionmaker(engine))

    async def exercise(mode):
        def scorer(dim, evidence, items):
            if mode == 'missing':
                return DimensionJudgement(evidence_status='missing', score=None, confidence='unavailable',
                    evidence_refs=[], missing_items=['缺少生产交付记录'], rationale='未找到相关记录')
            if mode == 'conflict':
                return DimensionJudgement(evidence_status='conflicting', score=None, confidence='unavailable',
                    evidence_refs=[items[0].ref_id], conflicts=['同一时期职责说法矛盾'], rationale='无法消解职责矛盾')
            return DimensionJudgement(evidence_status='sufficient', score=0 if mode == 'negative' else 3,
                confidence='high', evidence_refs=[items[0].ref_id], rationale='依据当前虚构原文')
        report = build_talent_evaluation_report_graph(dimension_scorer=scorer,
            claim_reviewer=None if mode == 'outage' else supported,
            report_writer=lambda candidates, items: ReportDraft(title='虚构评估', overview='未评分就是零分',
                candidates=[CandidateReportDraft(candidate_id='C1', summary='不会进入正式报告',
                    highlights=[ReportPoint(text='原文记载只参与原型，未负责生产上线', evidence_refs=[items[0].ref_id])])]))
        graph = build_talent_decision_engine(request_graph=one_node({'status': 'candidates_ready', 'candidate_ids': ['C1']}),
            evaluation_graph=one_node({'status': 'branches_ready', 'dimensions': [dimension()], 'branch_results': [branch()]}), report_graph=report)
        manager = TalentRunManager(graph=graph, record_store=history)
        first = await manager.create_run('虚构集成-'+mode)
        for _ in range(500):
            if first.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                break
            await asyncio.sleep(.01)
        assert first.status == RunStatus.COMPLETED
        event = next(e for e in first.events if e.type == 'report.completed')
        saved = history.get_run(first.run_id)
        assert event.data['assessment_grounding'] == saved['assessment_grounding']
        assert saved['conversation_turns'][0]['assessment_grounding'] == saved['assessment_grounding']
        snapshot = saved['assessment_grounding']
        score = snapshot['candidates'][0]['dimensions'][0]['score']
        assert score == (0 if mode == 'negative' else 3 if mode == 'normal' else None)
        assert '未评分就是零分' not in saved['report']
        if mode == 'outage':
            assert snapshot['status'] == 'degraded'
            assert snapshot['verification']['candidates']['C1']['technical_actions']
        if mode in {'missing', 'conflict'}:
            assert snapshot['verification']['candidates']['C1']['items']
        if mode == 'normal':
            second = await manager.create_run('虚构续问', conversation_id=first.conversation_id)
            for _ in range(500):
                if second.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                    break
                await asyncio.sleep(.01)
            assert second.status == RunStatus.COMPLETED
            assert len(history.get_run(second.run_id)['conversation_turns']) == 2
    for mode in ('normal', 'missing', 'negative', 'conflict', 'outage'):
        asyncio.run(exercise(mode))
    engine.dispose()


def test_graph_cancellation_reaches_running_review_coroutine(monkeypatch):
    from test_assessment_grounding import graph
    cancelled = []
    async def blocked(claims, anchors):
        try:
            await asyncio.sleep(20)
        finally:
            cancelled.append(True)
    async def exercise():
        task = asyncio.create_task(graph(blocked).ainvoke({'dimensions': [dimension()], 'branch_results': [branch()]}))
        await asyncio.sleep(.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(exercise())
    assert cancelled
