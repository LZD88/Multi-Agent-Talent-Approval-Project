import asyncio
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.assessment_grounding import AssessmentClaim, ClaimReviewer
from app.assessment_retry import AssessmentRetryRunner
from app.main import create_app
from app.talent_evaluation_report import (
    CandidateReportDraft, ConsistencyReview, DimensionJudgement, ReportDraft,
    ReportPoint, ScoringValidationError, build_talent_evaluation_report_graph,
)
from app.talent_run_stream import RunStatus, TalentRunManager
from test_assessment_grounding import FakeModel, anchor, branch, claim, dimension, supported
from test_grounding_integration import one_node
from test_talent_run_history import _store
from app.talent_decision_engine import build_talent_decision_engine


def test_partial_malformed_review_only_retries_failed_claims():
    evidence = anchor()
    requested = [claim(evidence).model_copy(update={"claim_id": f"c-{index}"}) for index in range(4)]

    async def response(payload):
        claims = [AssessmentClaim.model_validate(item) for item in payload["claims"]]
        result = supported(claims, []).model_dump(mode="json")
        if len(claims) == 4:
            result["reviews"][1]["verdict"] = "invalid"
            result["reviews"][2]["anchor_ids"] = ["outside"]
            result["reviews"].pop()
        return result

    model = FakeModel(response)
    result = asyncio.run(ClaimReviewer(lambda: model).areview(requested, [evidence]))
    assert [row.verdict for row in result.reviews] == ["supported"] * 4
    assert [[row["claim_id"] for row in call["claims"]] for call in model.calls] == [
        ["c-0", "c-1", "c-2", "c-3"], ["c-1", "c-2"], ["c-3"],
    ]


def test_local_retry_from_persisted_history_keeps_successful_scoring_and_report_review(monkeypatch):
    from app import talent_evaluation_report as report_module
    monkeypatch.setattr(report_module, "capture_call", lambda call, **kwargs: call())

    scoring_calls, review_calls, writer_calls = [], [], []
    fail_score, fail_claim = True, True

    def score(dim, evidence, items):
        scoring_calls.append(evidence.task_id)
        if fail_score and evidence.dimension_number == 2:
            raise ScoringValidationError("score_required", "test")
        return DimensionJudgement(evidence_status="sufficient", score=3, confidence="high",
                                  evidence_refs=[items[0].ref_id], rationale="参与原型")

    def review(claims, anchors):
        review_calls.extend(item.claim_id for item in claims)
        result = supported(claims, anchors)
        if fail_claim:
            for item in result.reviews:
                if item.claim_id == "C1:highlights:0":
                    item.verdict = "error"
        return result

    def write(candidates, items):
        writer_calls.append([item.candidate_id for item in candidates])
        return ReportDraft(title="虚构评估", overview="概要", candidates=[
            CandidateReportDraft(candidate_id=item.candidate_id, summary="概要",
                highlights=[ReportPoint(text="参与原型", evidence_refs=[items[0].ref_id])])
            for item in candidates
        ])

    components = dict(dimension_scorer=score, consistency_reviewer=lambda *args: ConsistencyReview(),
                      report_writer=write, claim_reviewer=review)
    dims = [dict(dimension(), weight_percent=50), dict(dimension(), name="职责核查", weight_percent=50)]
    second = dict(deepcopy(branch()), task_id="C1:2", dimension_number=2)
    report = build_talent_evaluation_report_graph(**components)
    graph = build_talent_decision_engine(
        request_graph=one_node({"status":"candidates_ready", "candidate_ids":["C1"]}),
        evaluation_graph=one_node({"status":"branches_ready", "dimensions":dims, "branch_results":[branch(), second]}),
        report_graph=report,
    )
    store = _store()
    initial_manager = TalentRunManager(graph=graph, record_store=store)

    async def start():
        run = await initial_manager.create_run("虚构局部重试")
        await run.task
        assert run.status == RunStatus.COMPLETED
        assert "assessment_retry" not in next(event for event in run.events if event.type == "report.completed").data
        return run.run_id

    run_id = asyncio.run(start())
    before = store.get_run(run_id)
    assert "assessment_retry" not in before
    by_kind = {item["kind"]: item for item in before["assessment_grounding"]["retry_items"]}
    assert by_kind["scoring"]["dimension_names"] == ["职责核查"]
    assert by_kind["scoring"]["reason_codes"] == ["score_required"]
    assert before["assessment_grounding"]["candidates"][0]["evidence_coverage_percent"] == 50
    assert sorted(scoring_calls) == ["C1:1", "C1:2"]

    restarted = TalentRunManager(graph=graph, record_store=store, retry_runner=AssessmentRetryRunner(**components))
    with TestClient(create_app(talent_run_manager=restarted)) as client:
        endpoint = f"/api/runs/{run_id}/assessment-retry"
        fail_claim = False
        review_calls.clear()
        response = client.post(endpoint, json={"item_id": by_kind["report_review"]["item_id"], "revision": 0})
        assert response.status_code == 200, response.text
        assert review_calls == ["C1:highlights:0"]
        assert writer_calls == [["C1"]]
        review_calls.clear()
        fail_score = False
        request = {"item_id": by_kind["scoring"]["item_id"], "revision": 1}
        assert client.post(endpoint, json={**request, "revision": 0}).status_code == 409
        response = client.post(endpoint, json=request)
        assert response.status_code == 200, response.text
        snapshot = response.json()["assessment_grounding"]
        assert snapshot["retry_revision"] == 2 and snapshot["retry_items"] == []
        assert snapshot["candidates"][0]["evidence_coverage_percent"] == 100
        assert snapshot["candidates"][0]["dimensions"][0] == before["assessment_grounding"]["candidates"][0]["dimensions"][0]
        assert scoring_calls[-1] == "C1:2" and len(scoring_calls) == 3
        assert "C1:1:score" not in review_calls
        history = client.get(f"/api/runs/history/{run_id}").json()
        assert history["assessment_grounding"] == history["conversation_turns"][0]["assessment_grounding"] == snapshot
        assert history["citations"] == before["citations"]
        assert client.post(endpoint, json=request).status_code == 409


def test_two_requests_cannot_retry_the_same_revision_at_once():
    from app.assessment_retry import RetryUnavailable

    class Store:
        def get_retry_state(self, run_id, revision):
            return {"version": 1}

        def save_retry_result(self, run_id, revision, result):
            pass

    class PendingRunner:
        async def retry(self, frozen, item_id, revision):
            started.set()
            await release.wait()
            return {"report": "修订报告", "assessment_grounding": {}}

    async def exercise():
        manager = TalentRunManager(record_store=Store(), retry_runner=PendingRunner())
        first = asyncio.create_task(manager.retry_assessment("run", "item", 0))
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(RetryUnavailable):
            await manager.retry_assessment("run", "item", 0)
        release.set()
        assert (await first)["report"] == "修订报告"

    started, release = asyncio.Event(), asyncio.Event()
    asyncio.run(exercise())
