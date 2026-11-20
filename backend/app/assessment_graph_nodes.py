"""Assessment graph adapters. Numeric/status text is always rendered by code."""
from __future__ import annotations

import asyncio
from collections import Counter

from langchain_core.runnables import RunnableLambda

from app.assessment_grounding import (
    AssessmentClaim, AssessmentGroundingSnapshot, EvidenceAnchor, error_review,
    review_with, rejected_review,
)
from app.assessment_verification import build_verification
from app.assessment_retry import freeze_retry_state, retry_items
from app.talent_decision_graph import DecisionContext


def anchors_from_state(state):
    return [EvidenceAnchor(evidence_id=row["ref_id"], candidate_id=row["candidate_id"],
        document_id=row.get("document_id", ""), document_version_id=row.get("document_version_id", ""),
        chunk_id=row.get("chunk_id") or row["ref_id"], quote_start=row.get("quote_start", 0),
        quote_end=row.get("quote_end") or len(row["quote"]), quote=row["quote"],
        citation_url=row["citation_url"], model_ref=row.get("model_ref") or "") for row in state.get("evidence_items", [])]


def context_from_state(state):
    data = state.get("assessment_context")
    return DecisionContext(**data) if data else None


async def review_with_reuse(reviewer, claims, state, *, phase):
    saved = state.get("reusable_reviews", {})
    retained, pending = {}, []
    for claim in claims:
        match = saved.get(claim.claim_id)
        # The list of related dimensions is display metadata for report facts.
        # Recheck current anchor eligibility in the caller before reusing their semantic verdict.
        comparable = claim.model_dump(mode="json")
        if match and phase == "report":
            comparable["dimension_numbers"] = match[0]["dimension_numbers"]
        if match and match[0] == comparable:
            retained[claim.claim_id] = match[1]
        else:
            pending.append(claim)
    result = await review_with(reviewer, pending, anchors_from_state(state), phase=phase,
                               context=context_from_state(state))
    retained.update({row.claim_id: row.model_dump(mode="json") for row in result.reviews})
    return [retained[claim.claim_id] for claim in claims]


def make_grounding_nodes(reviewer):
    async def dimensions(state):
        assessments = [dict(row) for row in state["dimension_assessments"]]
        claims, by_task = [], {}
        for row in assessments:
            if row["score"] is None:
                continue
            number = row["dimension_number"]
            dimension = state["dimensions"][number-1]
            facts = row.get("fact_claims", [])
            for index, fact in enumerate(facts):
                claims.append(AssessmentClaim(claim_id=f"{row['task_id']}:fact:{index}",
                    candidate_ids=[row["candidate_id"]], dimension_numbers=[number], text=fact["text"],
                    anchor_ids=fact["evidence_refs"]))
            claim = AssessmentClaim(claim_id=f"{row['task_id']}:score", candidate_ids=[row["candidate_id"]],
                dimension_numbers=[number], kind="score", anchor_ids=row["evidence_refs"],
                text=(f"维度：{dimension['name']}；定义：{dimension['definition']}；"
                      f"评分锚点：{dimension['score_anchors']}；拟评{row['score']}分；依据：{row['rationale']}"))
            claims.append(claim)
            by_task[row["task_id"]] = [item.claim_id for item in claims if item.claim_id.startswith(row["task_id"]+":")]
        reviews = await review_with_reuse(reviewer, claims, state, phase="dimension")
        by_id = {row["claim_id"]: row for row in reviews}
        for row in assessments:
            related = [by_id[key] for key in by_task.get(row["task_id"], [])]
            if any(item["verdict"] != "supported" for item in related):
                row.update(evidence_status="failed", score=None, confidence="unavailable",
                    reason_code="grounding_unavailable" if any(item["verdict"] == "error" for item in related) else "score_grounding_rejected",
                    rationale="评分依据尚未通过审查，本维度暂未评分。")
        return {"dimension_assessments": assessments, "original_assessments": state["dimension_assessments"],
                "grounding_claims": [row.model_dump(mode="json") for row in claims],
                "grounding_reviews": reviews, "status": "dimensions_grounded"}

    async def report(state):
        claims = []
        candidate_assessments = {row["candidate_id"]: row for row in state["candidate_assessments"]}
        for candidate in state["report_draft"].get("candidates", []):
            candidate_id = candidate["candidate_id"]
            allowed = candidate_assessments.get(candidate_id, {}).get("dimensions", [])
            for section in ("highlights", "risk_points"):
                for index, point in enumerate(candidate.get(section, [])):
                    numbers = [row["dimension_number"] for row in allowed
                               if row["score"] is not None and set(point["evidence_refs"]) & set(row["evidence_refs"])]
                    claims.append(AssessmentClaim(claim_id=f"{candidate_id}:{section}:{index}",
                        candidate_ids=[candidate_id], dimension_numbers=numbers, text=point["text"],
                        anchor_ids=point["evidence_refs"]))
        # A source existing in the run is insufficient: it must also be among this candidate's scored references.
        rejected, pending = [], []
        for claim in claims:
            candidate = candidate_assessments.get(claim.candidate_ids[0], {})
            refs = {ref for dimension in candidate.get("dimensions", []) if dimension["score"] is not None
                    for ref in dimension["evidence_refs"]}
            if not set(claim.anchor_ids).issubset(refs):
                rejected.append(rejected_review(claim, "unscored_or_unknown_anchor"))
            else:
                pending.append(claim)
        reviews = await review_with_reuse(reviewer, pending, state, phase="report")
        reviews.extend(row.model_dump(mode="json") for row in rejected)
        return {"grounding_claims": state.get("grounding_claims", [])+[row.model_dump(mode="json") for row in claims],
                "grounding_reviews": state.get("grounding_reviews", [])+reviews,
                "status": "report_grounded"}

    return (RunnableLambda(lambda state: asyncio.run(dimensions(state)), afunc=dimensions),
            RunnableLambda(lambda state: asyncio.run(report(state)), afunc=report))


def verification_node(state):
    checklist = build_verification(state["dimensions"], state["dimension_assessments"],
                state["grounding_claims"], state["grounding_reviews"])
    for candidate, group in checklist["candidates"].items():
        if state.get("report_error") or candidate in state.get("candidate_report_errors", {}):
            group["technical_actions"].append({"item_id": "report-writer", "reason_type": "technical",
                "reason_code": "report_writer_unavailable", "question": "报告文字生成暂未完成，需要重试。"})
    return {"verification": checklist, "status": "verification_ready"}


def render_grounded_report(state):
    from app.config import get_settings
    reviews = state.get("grounding_reviews", [])
    counts = Counter(row["verdict"] for row in reviews)
    technical_failure = any(row["evidence_status"] == "failed" and row.get("reason_code") != "score_grounding_rejected"
                            for row in state["dimension_assessments"])
    report_error = bool(state.get("report_error") or state.get("candidate_report_errors"))
    if counts["error"] or report_error or technical_failure:
        status = "degraded"
    elif any(row["score"] is None for row in state["dimension_assessments"]) or counts["supported"] != len(reviews):
        status = "partial"
    else:
        status = "complete"
    by_claim = {row["claim_id"]: row for row in state["grounding_claims"]}
    evidence = {row["ref_id"]: row for row in state["evidence_items"]}
    def citations(refs):
        return " ".join(f"[{evidence[key]['model_ref']}]({evidence[key]['citation_url']})" for key in refs if key in evidence)
    lines = ["# 人才材料评估报告", "", "以下为当前材料评估顺序，材料中的陈述尚需独立核实。",
             "理论上限是未评分维度取满分时的计算边界，不是预测得分。", "",
             f"依据审查：{'降级' if status == 'degraded' else '部分完成' if status == 'partial' else '完成'}；"
             f"通过 {counts['supported']} 项，拦截 {sum(counts.values())-counts['supported']-counts['error']} 项，审查错误 {counts['error']} 项。"]
    for rank, candidate in enumerate(state["candidate_assessments"], 1):
        cid = candidate["candidate_id"]
        score_line = (f"已确认得分 **{candidate['confirmed_score']:.1f}** / 100，" if candidate['evidence_coverage_percent'] else
                      "当前材料无法完成评估，所有维度均未评分；")
        lines.extend(["", f"## {rank}. {cid}", "", score_line+
            f"理论上限 **{candidate['possible_score']:.1f}** / 100，可评分覆盖率 **{candidate['evidence_coverage_percent']}%**",
            "", "### 维度评分", ""])
        for row in candidate["dimensions"]:
            name = state["dimensions"][row["dimension_number"]-1]["name"]
            labels = {"sufficient": "证据充分", "partial": "证据部分覆盖", "missing": "材料不足", "conflicting": "材料冲突", "failed": "评估暂未完成"}
            value = f"{row['score']} 分" if row["score"] is not None else "未评分"
            lines.append(f"- {name}：{value}（{labels[row['evidence_status']]}）")
        if candidate["evidence_coverage_percent"] < 70:
            lines.extend(["", "当前材料覆盖率较低，排序仅反映已有可评分依据，建议先补充核实。"])
        lines.extend(["", "### 主要依据", ""])
        approved = [by_claim[row["claim_id"]] for row in reviews if row["verdict"] == "supported"
                    and by_claim[row["claim_id"]]["candidate_ids"] == [cid]
                    and ":highlights:" in row["claim_id"]]
        lines.extend([f"- {row['text']} {citations(row['anchor_ids'])}" for row in approved] or ["当前暂无通过审查的报告依据。"])
        risks = [by_claim[row["claim_id"]] for row in reviews if row["verdict"] == "supported"
                 and by_claim[row["claim_id"]]["candidate_ids"] == [cid] and ":risk_points:" in row["claim_id"]]
        if risks:
            lines.extend(["", "### 材料中的风险依据", ""]+[f"- {row['text']} {citations(row['anchor_ids'])}" for row in risks])
        checklist = state["verification"]["candidates"].get(cid, {})
        lines.extend(["", "### 待核实事项", ""])
        for row in checklist.get("items", []):
            lines.append(f"- {row['question']} {row['expected_material']} {citations(row['anchor_ids'])}")
            for detail in row.get("trigger_details", []):
                # Render source text as a quoted pending record, never an approved assertion.
                safe_detail = str(detail).replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')
                lines.append(f"  - 待核实记录（非已确认事实）：{safe_detail}")
        if not checklist.get("items"):
            lines.append("当前没有待核实事项。")
        if checklist.get("omitted_count"):
            lines.append(f"另有 {checklist['omitted_count']} 项，按优先级显示前八项。")
        if checklist.get("technical_actions"):
            lines.extend(["", "### 技术重试事项", ""]+[f"- {row['question']}" for row in checklist["technical_actions"]])
    options = retry_items(state)
    revision = state.get("retry_revision", 0)
    snapshot = AssessmentGroundingSnapshot(status=status, model=get_settings().grounding_model or get_settings().chat_model,
        claims=state["grounding_claims"], reviews=reviews, anchors=anchors_from_state(state),
        original_assessments=state["original_assessments"], candidates=state["candidate_assessments"],
        verification=state["verification"], retry_items=options, retry_revision=revision,
        summary={"submitted": len(reviews), "supported": counts["supported"],
            "blocked": sum(counts.values())-counts["supported"]-counts["error"], "errors": counts["error"],
            "report_error": report_error})
    return {"report": "\n".join(lines).strip(), "assessment_grounding": snapshot.model_dump(mode="json"),
            "assessment_retry": freeze_retry_state(state) if options else None, "status": "completed"}
