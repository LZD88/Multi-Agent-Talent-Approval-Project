"""Transparent metrics against confirmed labels, separate from optional LLM Judge."""
from collections import Counter

from app.assessment_grounding import AssessmentClaim, EvidenceAnchor, GroundingReview, check_claim

NEW_MODULES = {"dimension_scoring", "report_grounding"}


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None,
            "unavailable_reason": None if denominator else "no_applicable_samples"}


def quality_metrics(rows):
    counts = Counter()
    for row in rows:
        if not row.get("confirmed"):
            counts["unconfirmed"] += 1
            continue
        counts["requested"] += 1
        status = row.get("status", "completed")
        counts[status] += 1
        module = row["module_key"]
        expected, actual = row["golden"], row.get("actual")
        if module == "dimension_scoring":
            counts["dimensions"] += 1
            if actual is not None and status == "completed":
                expected_eligible = expected["score"] is not None and expected["evidence_status"] in {"sufficient", "partial"}
                actual_eligible = actual["score"] is not None and actual["evidence_status"] in {"sufficient", "partial"}
                counts["dimensions_correct"] += expected_eligible == actual_eligible and expected["evidence_status"] == actual["evidence_status"]
        elif module == "report_grounding":
            golden = GroundingReview.model_validate(expected)
            observed = {item["claim_id"]: item for item in (actual or {}).get("reviews", [])}
            if status == "completed" and any(item.get("verdict") == "error" for item in observed.values()):
                counts["completed"] -= 1
                counts["execution_failed"] += 1
            inputs = row.get("input_payload", {})
            anchors = {item.evidence_id: item for raw in inputs.get("anchors", [])
                       if (item := EvidenceAnchor.model_validate(raw))}
            invalid = set()
            for raw in inputs.get("claims", []):
                claim = AssessmentClaim.model_validate(raw)
                valid = check_claim(claim, anchors) is None
                counts["references_submitted"] += len(claim.anchor_ids)
                counts["references_valid"] += len(claim.anchor_ids) if valid else 0
                if not valid:
                    invalid.add(claim.claim_id)
            for reference in golden.reviews:
                result = observed.get(reference.claim_id, {})
                published = result.get("verdict") == "supported" and status == "completed"
                good = reference.verdict == "supported"
                counts["published"] += published
                counts["illegal_reference_published"] += published and reference.claim_id in invalid
                counts["published_supported"] += published and good
                counts["supported"] += good
                counts["false_blocks"] += good and not published
                counts["unsupported"] += not good
                counts["unsafe_blocks"] += not good and not published
                counts["review_errors"] += result.get("verdict") == "error"
            # Unknown/duplicate IDs cannot improve metrics or count as completion.
            ids = [item["claim_id"] for item in (actual or {}).get("reviews", [])]
            if actual is not None and (set(ids) != {item.claim_id for item in golden.reviews} or len(ids) != len(set(ids))):
                counts["protocol_errors"] += 1
    applicable = {"score_eligibility_agreement": ratio(counts["dimensions_correct"], counts["dimensions"]),
                  "published_claim_support": ratio(counts["published_supported"], counts["published"]),
                  "unsupported_claim_blocking": ratio(counts["unsafe_blocks"], counts["unsupported"]),
                  "supported_claim_false_blocking": ratio(counts["false_blocks"], counts["supported"]),
                  "reference_validation": ratio(counts["references_valid"], counts["references_submitted"])}
    complete = bool(counts["requested"]) and counts["completed"] == counts["requested"] and not any(
        counts[key] for key in ("review_errors", "protocol_errors", "unconfirmed"))
    return {"counts": dict(counts), "complete": complete, "metrics": applicable}
