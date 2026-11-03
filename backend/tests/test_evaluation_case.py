from __future__ import annotations

from langchain.messages import HumanMessage

from app.evaluation_case import (
    EvaluationCaseDraft,
    EvaluationModule,
    build_request_understanding_case,
)


def test_first_evaluation_modules_are_closed_and_explicit():
    assert {item.value for item in EvaluationModule} == {
        "request_understanding",
        "job_matching",
        "dimension_generation",
        "fact_extraction",
    }


def test_request_understanding_case_keeps_replayable_input_and_actual_output():
    case = build_request_understanding_case(
        source_run_id="run-22",
        conversation_id="conversation-22",
        turn_no=5,
        messages=[HumanMessage(content=f"第 {turn} 轮要求") for turn in range(1, 6)],
        previous_request={"hard_conditions": [{"field": "region", "value": "上海"}]},
        conversation_title="AI 人才筛选",
        output_payload={
            "input_mode": "detailed_requirement",
            "semantic_requirements": ["Agent 评测经验"],
        },
        replay_context={"tenant_id": "course-demo", "permission_scopes": ["hr_private"]},
        version_snapshot={"prompt": "request-understanding-v1", "model": "fixture-model"},
    )

    assert isinstance(case, EvaluationCaseDraft)
    assert case.module_key is EvaluationModule.REQUEST_UNDERSTANDING
    assert case.conversation_id == "conversation-22"
    assert case.turn_no == 5
    assert [item["content"] for item in case.input_payload["messages"]] == [
        "第 2 轮要求",
        "第 3 轮要求",
        "第 4 轮要求",
        "第 5 轮要求",
    ]
    assert case.input_payload["previous_request"]["hard_conditions"][0]["value"] == "上海"
    assert case.output_payload["semantic_requirements"] == ["Agent 评测经验"]
    assert case.replay_context["tenant_id"] == "course-demo"
    assert case.version_snapshot["model"] == "fixture-model"
    assert case.model_dump(mode="json")["schema_version"] == "1.0"
