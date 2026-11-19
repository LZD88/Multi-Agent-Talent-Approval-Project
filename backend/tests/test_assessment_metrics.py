from app.assessment_metrics import quality_metrics


def golden():
    return {"reviews": [{"claim_id": "good", "verdict": "supported", "reason_code": "direct", "reason": "支持", "anchor_ids": ["a"]},
                        {"claim_id": "bad", "verdict": "insufficient", "reason_code": "missing", "reason": "未提及", "anchor_ids": ["a"]}]}


def test_all_blocked_cannot_fake_perfect_precision():
    expected = golden()
    actual = {"reviews": [{**row, "verdict": "insufficient"} for row in expected["reviews"]]}
    result = quality_metrics([dict(module_key="report_grounding", confirmed=True, golden=expected, actual=actual)])
    assert result["metrics"]["published_claim_support"]["value"] is None
    assert result["metrics"]["supported_claim_false_blocking"]["value"] == 1


def test_failure_remains_in_denominator_and_unconfirmed_is_not_golden():
    result = quality_metrics([dict(module_key="report_grounding", confirmed=True, golden=golden(), actual=None, status="execution_failed"),
                             dict(module_key="report_grounding", confirmed=False, golden=golden(), actual=golden())])
    assert not result["complete"]
    assert result["counts"]["execution_failed"] == 1
    assert result["counts"]["unconfirmed"] == 1
    assert result["metrics"]["supported_claim_false_blocking"]["denominator"] == 1


def test_unknown_and_duplicate_ids_incomplete():
    actual = golden()
    actual["reviews"] += [actual["reviews"][0]]
    assert not quality_metrics([dict(module_key="report_grounding", confirmed=True, golden=golden(), actual=actual)])["complete"]


def test_actual_zero_is_eligible_unscored_is_not():
    result = quality_metrics([dict(module_key="dimension_scoring", confirmed=True,
        golden={"score": None, "evidence_status": "missing"}, actual={"score": 0, "evidence_status": "sufficient"})])
    assert result["metrics"]["score_eligibility_agreement"]["value"] == 0
