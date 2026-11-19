import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.assessment_grounding import (
    AssessmentClaim, AssessmentGroundingSnapshot, ClaimReview, ClaimReviewer,
    EvidenceAnchor, GroundingReview, source_ref, validate_review,
)


def anchor(candidate="C1", start=0, quote="只参与原型，未负责生产上线", chunk="chunk"):
    row = dict(document_id="doc", document_version_id="version", chunk_id=chunk,
               quote_start=start, quote_end=start+len(quote), quote=quote)
    return EvidenceAnchor(evidence_id=source_ref(candidate, row), candidate_id=candidate,
                          citation_url=f"/api/evidence/citations/{chunk}?quote_start={start}&quote_end={row['quote_end']}", **row)


def claim(a=None, text="没有负责该项目的生产上线", **kwargs):
    a = a or anchor()
    return AssessmentClaim(claim_id="claim-1", candidate_ids=[a.candidate_id], text=text,
                           anchor_ids=[a.evidence_id], dimension_numbers=[1], **kwargs)


def supported(claims, anchors):
    return GroundingReview(reviews=[ClaimReview(claim_id=row.claim_id, verdict="supported",
        reason_code="direct", reason="原文支持限定命题", anchor_ids=row.anchor_ids) for row in claims])


def test_anchor_keeps_two_spans_and_rejects_forged_version_or_span():
    a, b = anchor(), anchor(start=25, quote="承担接口开发")
    assert a.evidence_id != b.evidence_id
    for update in ({"quote_end": 1}, {"document_version_id": "new"}):
        with pytest.raises(ValidationError):
            EvidenceAnchor.model_validate({**a.model_dump(), **update})


def test_review_allows_supported_negative_and_requires_exact_ids():
    a = anchor()
    result = supported([claim(a)], [a])
    validate_review(result, [claim(a)])
    assert result.reviews[0].verdict == "supported"
    for rows in ([], result.reviews*2, [result.reviews[0].model_copy(update={"anchor_ids": ["foreign"]})]):
        with pytest.raises(ValueError):
            validate_review(GroundingReview(reviews=rows), [claim(a)])


def test_unknown_snapshot_version_rejected():
    with pytest.raises(ValidationError):
        AssessmentGroundingSnapshot.model_validate({"schema_version": "2.0"})


class FakeModel:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def with_structured_output(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages):
        import json
        payload = json.loads(messages[1][1])
        self.calls.append(payload)
        return await self.responder(payload)


def test_reviewer_bounded_retry_fail_closed_and_source_owner_validation():
    async def invalid(payload):
        return {"reviews": []}
    model = FakeModel(invalid)
    a = anchor()
    reviewer = ClaimReviewer(lambda: model)
    result = asyncio.run(reviewer.areview([claim(a)], [a]))
    assert len(model.calls) == 2
    assert result.reviews[0].verdict == "error"
    model.calls.clear()
    wrong = claim(a).model_copy(update={"candidate_ids": ["other"]})
    result = asyncio.run(reviewer.areview([wrong], [a]))
    assert not model.calls
    assert result.reviews[0].reason_code == "candidate_mismatch"


def test_request_timeout_and_cancellation_cancel_actual_coroutine():
    cancelled = []
    async def blocked(payload):
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.append(True)
    a = anchor()
    model = FakeModel(blocked)
    reviewer = ClaimReviewer(lambda: model, request_timeout=.01, batch_timeout=.015)
    result = asyncio.run(reviewer.areview([claim(a)], [a]))
    assert result.reviews[0].verdict == "error" and cancelled
    async def cancel():
        task = asyncio.create_task(reviewer.areview([claim(a)], [a]))
        await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())


def test_batch_isolation_and_concurrency_limit():
    active, peak = 0, 0
    async def respond(payload):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.005)
        owners = {row["candidate_id"] for row in payload["anchors"]}
        assert len(owners) == 1
        active -= 1
        return supported([AssessmentClaim.model_validate(row) for row in payload["claims"]], []).model_dump()
    model = FakeModel(respond)
    anchors = [anchor(f"C{index}") for index in range(6)]
    claims = [claim(a).model_copy(update={"claim_id": f"c-{index}"}) for index, a in enumerate(anchors)]
    result = asyncio.run(ClaimReviewer(lambda: model, concurrency=2).areview(claims, anchors))
    assert peak == 2 and len(result.reviews) == 6


def dimension():
    return {"name": "项目交付", "definition": "明确职责与结果", "weight_percent": 100,
            "evidence_requirements": ["交付事实"], "score_anchors": [
                {"score": 0, "description": "明确未满足"}, {"score": 3, "description": "参与原型"},
                {"score": 5, "description": "主导生产"}], "retrieval_hints": ["项目"], "source_requirement_ids": ["S1"]}


def branch():
    a = anchor()
    source = a.model_dump(include={"document_id", "document_version_id", "chunk_id", "quote", "quote_start", "quote_end"})
    source.update(citation_id="chunk", source_label="虚构简历")
    return {"task_id": "C1:1", "candidate_id": "C1", "dimension_number": 1,
        "execution_status": "succeeded", "requirements": [{"requirement_id": "S1", "query": "项目",
        "status": "sufficient", "reason": "found", "extraction_status": "succeeded",
        "facts": [{"event": "虚构项目", "period": "2025", "claim": "参与原型", "answer": "yes", "sources": [source]}]}]}


def graph(reviewer, score=3):
    from app.talent_evaluation_report import DimensionJudgement, ReportDraft, CandidateReportDraft, ReportPoint, build_talent_evaluation_report_graph
    return build_talent_evaluation_report_graph(claim_reviewer=reviewer,
        dimension_scorer=lambda dimension, branch, items: DimensionJudgement(evidence_status="sufficient",
            score=score, confidence="high", evidence_refs=[items[0].ref_id], rationale="参与原型"),
        report_writer=lambda candidates, items: ReportDraft(title="错误的任命结论", overview="未评分就是零分",
            candidates=[CandidateReportDraft(candidate_id="C1", summary="没有管理能力", risks=["能力不行"],
                highlights=[ReportPoint(text="参与原型", evidence_refs=[items[0].ref_id])])]))


def test_graph_suppresses_unchecked_prose_keeps_actual_zero_and_exact_anchor():
    result = graph(supported, score=0).invoke({"dimensions": [dimension()], "branch_results": [branch()]})
    assert result["candidate_assessments"][0]["confirmed_score"] == 0
    assert result["candidate_assessments"][0]["evidence_coverage_percent"] == 100
    assert "0 分" in result["report"]
    assert "没有管理能力" not in result["report"] and "未评分就是零分" not in result["report"]
    assert result["assessment_grounding"]["anchors"][0]["document_version_id"] == "version"


def test_graph_semantic_rejection_clears_score_before_aggregation_and_has_checklist():
    def reject(claims, anchors):
        return GroundingReview(reviews=[ClaimReview(claim_id=row.claim_id, verdict="insufficient",
            reason_code="prototype_not_delivery", reason="原型不足以证明生产上线", anchor_ids=row.anchor_ids) for row in claims])
    result = graph(reject).invoke({"dimensions": [dimension()], "branch_results": [branch()]})
    assert result["candidate_assessments"][0]["evidence_coverage_percent"] == 0
    assert result["dimension_assessments"][0]["score"] is None
    checklist = result["verification"]["candidates"]["C1"]
    assert checklist["items"] and not checklist["technical_actions"]
    assert "未评分" in result["report"]


def test_graph_unconfigured_review_fails_closed_and_async_matches_sync():
    result = asyncio.run(graph(None).ainvoke({"dimensions": [dimension()], "branch_results": [branch()]}))
    assert result["assessment_grounding"]["status"] == "degraded"
    assert result["dimension_assessments"][0]["score"] is None
    assert result["verification"]["candidates"]["C1"]["technical_actions"]


def test_review_recorder_failure_does_not_change_successful_business_result():
    async def respond(payload):
        return supported([AssessmentClaim.model_validate(row) for row in payload["claims"]], []).model_dump()
    def fail(draft):
        raise RuntimeError("storage unavailable")
    a = anchor()
    context = SimpleNamespace(run_id="synthetic", conversation_id="synthetic", turn_no=1,
                              tenant_id="course-demo", permission_scopes=("hr_private",))
    result = asyncio.run(ClaimReviewer(lambda: FakeModel(respond), recorder=fail).areview([claim(a)], [a], context=context))
    assert result.reviews[0].verdict == "supported"


def test_nonrecoverable_billing_error_is_not_retried():
    class BillingError(Exception):
        status_code = 400
    async def fail(payload):
        raise BillingError('Arrearage')
    model = FakeModel(fail)
    a = anchor()
    result = asyncio.run(ClaimReviewer(lambda: model).areview([claim(a)], [a]))
    assert len(model.calls) == 1
    assert result.reviews[0].verdict == 'error'


def test_grounding_clients_are_independent_and_disable_hidden_retries():
    from app.model_provider import get_grounding_model
    from app.config import Settings
    settings = Settings(_env_file=None, chat_api_key='synthetic-not-a-real-key', chat_model='qwen-plus')
    first, second = get_grounding_model(settings), get_grounding_model(settings)
    assert first.max_retries == second.max_retries == 0
    assert first.http_async_client is not second.http_async_client
    async def close():
        await first.root_async_client.close()
        assert not second.http_async_client.is_closed
        await second.root_async_client.close()
    asyncio.run(close())


def test_bad_report_reference_never_reaches_reviewer_and_is_not_technical_outage():
    calls = []
    def review(claims, anchors):
        calls.extend(claims)
        return supported(claims, anchors)
    from app.assessment_grounding import review_with
    a = anchor()
    wrong = claim(a).model_copy(update={"candidate_ids": ["C2"]})
    result = asyncio.run(review_with(review, [wrong], [a], phase="report"))
    assert not calls
    assert result.reviews[0].verdict == "insufficient"


def test_report_reviewer_exception_is_degraded_without_leaking_draft():
    def review(claims, anchors):
        if any(":highlights:" in row.claim_id for row in claims):
            raise RuntimeError("outage")
        return supported(claims, anchors)
    result = graph(review).invoke({"dimensions": [dimension()], "branch_results": [branch()]})
    assert result["candidate_assessments"][0]["evidence_coverage_percent"] == 100
    assert result["assessment_grounding"]["status"] == "degraded"
    assert result["verification"]["candidates"]["C1"]["technical_actions"]


def test_same_chunk_two_quotes_survive_report_mapping_and_model_aliases():
    from app.talent_evaluation_report import evidence_items_from_branches, _model_evidence_payload
    from app.talent_evaluation_dispatch import BranchEvidenceDraft
    raw = branch()
    second = anchor(start=30, quote="完成接口测试")
    source = second.model_dump(include={"document_id", "document_version_id", "chunk_id", "quote", "quote_start", "quote_end"})
    source.update(citation_id="chunk", source_label="虚构简历")
    raw['requirements'][0]['facts'][0]['sources'].append(source)
    items = evidence_items_from_branches([BranchEvidenceDraft.model_validate(raw)])
    assert len(items) == 2 and len({r.ref_id for r in items}) == 2
    payload, to_model, from_model = _model_evidence_payload(items)
    assert set(to_model.values()) == {'E1', 'E2'}
    assert len(set(r.citation_url for r in items)) == 2
    assert set(from_model.values()) == {r.ref_id for r in items}


@pytest.mark.parametrize('text', ['同一个项目算两个独立项目', '2022年主导2025年的上线', '管理能力弱于没有材料的C2'])
def test_report_claims_and_fact_risks_are_filtered_after_scoring(text):
    from app.talent_evaluation_report import DimensionJudgement, ReportDraft, CandidateReportDraft, ReportPoint, build_talent_evaluation_report_graph
    def review(claims, anchors):
        return GroundingReview(reviews=[ClaimReview(claim_id=row.claim_id,
            verdict='insufficient' if ':risk_points:' in row.claim_id or ':highlights:' in row.claim_id else 'supported',
            reason_code='scope_not_supported', reason='限定与当前材料不符', anchor_ids=row.anchor_ids) for row in claims])
    result = build_talent_evaluation_report_graph(claim_reviewer=review,
        dimension_scorer=lambda dim, branch, items: DimensionJudgement(evidence_status='sufficient', score=3,
            confidence='high', evidence_refs=[items[0].ref_id], rationale='参与原型'),
        report_writer=lambda candidates, items: ReportDraft(title='虚构报告', overview='不可绕过门禁', candidates=[
            CandidateReportDraft(candidate_id='C1', summary='不可绕过门禁', highlights=[ReportPoint(text=text, evidence_refs=[items[0].ref_id])],
                risk_points=[ReportPoint(text='没有任何管理能力', evidence_refs=[items[0].ref_id])])])).invoke(
                    {'dimensions': [dimension()], 'branch_results': [branch()]})
    assert result['dimension_assessments'][0]['score'] == 3
    # Rejected text may be preserved only inside explicitly labelled pending records.
    main = result['report'].split('### 待核实事项')[0]
    assert text not in main and '没有任何管理能力' not in main
    assert result['assessment_grounding']['summary']['blocked'] == 2
