from app.assessment_verification import build_verification


def assessment(**update):
    return {"candidate_id": "C1", "dimension_number": 1, "task_id": "C1:1", "score": None,
            "evidence_status": "missing", "missing_items": ["缺少数据", "缺少数据"], "conflicts": [], **update}


def test_checklist_deduplicates_caps_and_does_not_invent_citations():
    dimensions = [{"name": "交付", "weight_percent": 100}]
    rows = [assessment(missing_items=[f"缺失{i}" for i in range(12)])]
    result = build_verification(dimensions, rows, [], [])
    group = result["candidates"]["C1"]
    assert len(group["items"]) == 8 and group["omitted_count"] == 4
    assert all(not row["anchor_ids"] for row in group["items"])
    assert result == build_verification(dimensions, rows, [], [])
    assert len(build_verification(dimensions, [assessment()], [], [])["candidates"]["C1"]["items"]) == 1


def test_conflicting_retains_both_sources_and_technical_failure_has_no_questions():
    dimensions = [{"name": "管理", "weight_percent": 100}]
    conflict = assessment(evidence_status="conflicting", missing_items=[], conflicts=["职责矛盾"], evidence_refs=["a", "b"])
    item = build_verification(dimensions, [conflict], [], [])["candidates"]["C1"]["items"][0]
    assert item["anchor_ids"] == ["a", "b"] and "澄清" in item["question"]
    group = build_verification(dimensions, [assessment(evidence_status="failed")], [], [])["candidates"]["C1"]
    assert not group["items"] and len(group["technical_actions"]) == 1
