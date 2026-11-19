"""Deterministic read-only interview checklists, separate from technical failures."""
from app.assessment_grounding import stable_id

TEMPLATE_VERSION = "verification-templates-v1"


def build_verification(dimensions, assessments, claims, reviews):
    by_candidate = {}
    claim_by_id = {row["claim_id"]: row for row in claims}
    rejected = {}
    for review in reviews:
        claim = claim_by_id[review["claim_id"]]
        if review["verdict"] not in {"supported", "error"}:
            for candidate in claim["candidate_ids"]:
                for number in claim["dimension_numbers"]:
                    rejected.setdefault((candidate, number), []).append((claim, review))

    for assessment in assessments:
        candidate, number = assessment["candidate_id"], assessment["dimension_number"]
        group = by_candidate.setdefault(candidate, {"items": [], "technical_actions": [], "omitted_count": 0})
        dimension = dimensions[number-1]
        trigger = assessment["task_id"]
        reason = assessment.get("reason_code")
        if assessment["evidence_status"] == "failed" and reason != "score_grounding_rejected":
            group["technical_actions"].append({"item_id": stable_id(trigger, "technical"),
                "candidate_id": candidate, "dimension_numbers": [number], "trigger_ids": [trigger],
                "reason_type": "technical", "reason_code": reason or "evaluation_failed",
                "question": f"{dimension['name']}评分暂未完成，需要重试。"})
            continue
        triggers = []
        if assessment["conflicts"]:
            triggers.append(("conflicting", trigger+":conflict", assessment.get("evidence_refs", [])))
        for missing in dict.fromkeys(assessment["missing_items"]):
            if reason != "score_grounding_rejected":
                triggers.append(("missing", stable_id(trigger, missing), []))
        for claim, review in rejected.get((candidate, number), []):
            triggers.append(("unsupported", claim["claim_id"], claim["anchor_ids"]))
        for kind, record, anchors in triggers:
            text = {"conflicting": "材料对相关职责或结果存在不同说法，请澄清项目、时期和职责范围。",
                    "missing": "请补充说明相关项目、时期、职责与结果。",
                    "unsupported": "当前判断尚缺少直接依据，请说明实际职责与交付范围。"}[kind]
            group["items"].append({"item_id": stable_id(candidate, number, kind, record),
                "candidate_id": candidate, "dimension_numbers": [number], "reason_type": kind,
                "trigger_ids": [record], "anchor_ids": list(dict.fromkeys(anchors)),
                "trigger_details": ([missing for missing in assessment["missing_items"]
                    if stable_id(trigger, missing) == record] if kind == "missing" else
                    assessment["conflicts"] if kind == "conflicting" else
                    [claim_by_id[record]["text"]]),
                "question": f"关于{dimension['name']}：{text}",
                "expected_material": "请提供可核实的职责分工、项目记录或效果指标。",
                "priority": dimension["weight_percent"]})
    # Report-only rejections may have no scored dimension. Retain their trace and
    # separate technical errors even when no interview item can be inferred.
    for review in reviews:
        claim = claim_by_id[review["claim_id"]]
        if review["verdict"] == "supported" or ":score" in claim["claim_id"] or ":fact:" in claim["claim_id"]:
            continue
        for candidate in claim["candidate_ids"]:
            group = by_candidate.setdefault(candidate, {"items": [], "technical_actions": [], "omitted_count": 0})
            if review["verdict"] == "error":
                group["technical_actions"].append({"item_id": stable_id(claim["claim_id"], "technical"),
                    "reason_type": "technical", "trigger_ids": [claim["claim_id"]],
                    "reason_code": review["reason_code"], "question": "报告命题依据审查暂未完成，需要重试。"})
            elif not claim["dimension_numbers"]:
                group["items"].append({"item_id": stable_id(candidate, claim["claim_id"]),
                    "candidate_id": candidate, "dimension_numbers": [], "reason_type": "unsupported",
                    "trigger_ids": [claim["claim_id"]], "trigger_details": [claim["text"]],
                    "anchor_ids": claim["anchor_ids"], "question": "请澄清相关项目、时期和实际职责范围。",
                    "expected_material": "请提供可核实的项目与职责材料。", "priority": 0})
    order = {"conflicting": 0, "missing": 1, "unsupported": 2}
    for group in by_candidate.values():
        unique = {row["item_id"]: row for row in group["items"]}
        rows = sorted(unique.values(), key=lambda row: (-row["priority"], order[row["reason_type"]], row["item_id"]))
        group["items"], group["omitted_count"] = rows[:8], max(0, len(rows)-8)
    return {"template_version": TEMPLATE_VERSION, "candidates": by_candidate}
