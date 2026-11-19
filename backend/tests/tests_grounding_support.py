"""Explicit trusted fake for graph orchestration tests; no production fallback."""
from app.assessment_grounding import GroundingReview, ClaimReview


def supported_reviewer(claims, anchors):
    return GroundingReview(reviews=[ClaimReview(claim_id=row.claim_id, verdict="supported",
        reason_code="test_fixture", reason="固定测试审查结果", anchor_ids=row.anchor_ids) for row in claims])
