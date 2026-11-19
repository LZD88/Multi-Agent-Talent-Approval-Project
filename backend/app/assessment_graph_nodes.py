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
        result = await review_with(reviewer, claims, anchors_from_state(state), phase="dimension", context=context_from_state(state))
        by_id = {row.claim_id: row for row in result.reviews}
        for row in assessments:
            related = [by_id[key] for key in by_task.get(row["task_id"], [])]
            if any(item.verdict != "supported" for item in related):
                row.update(evidence_status="failed", score=None, confidence="unavailable",
                    reason_code="grounding_unavailable" if any(item.verdict == "error" for item in related) else "score_grounding_rejected",
                    rationale="评分依据尚未通过审查，本维度暂未评分。")
        return {"dimension_assessments": assessments, "original_assessments": state["dimension_assessments"],
                "grounding_claims": [row.model_dump(mode="json") for row in claims],
                "grounding_reviews": [row.model_dump(mode="json") for row in result.reviews], "status": "dimensions_grounded"}

    return RunnableLambda(lambda state: asyncio.run(dimensions(state)), afunc=dimensions)
