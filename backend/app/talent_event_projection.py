from __future__ import annotations

from typing import Any


STEP_BY_START_NODE = {
    "receive_request": ("understand_request", "workflow", "理解人才要求"),
    "interpret_input": ("understand_request", "workflow", "理解人才要求"),
    "filter_candidates": ("filter_candidates", "tool", "筛选候选人"),
    "generate_dimensions": ("build_dimensions", "workflow", "生成评估维度"),
    "prepare_work_items": (
        "parallel_assessment",
        "parallel",
        "检索证据并并行评估",
    ),
    "run_assessment_branch": (
        "parallel_assessment",
        "parallel",
        "检索证据并并行评估",
    ),
    "score_dimensions": ("score_candidates", "workflow", "证据质量门禁与排名"),
    "compose_report": ("compose_report", "workflow", "合成可引用评估报告"),
}


STEP_BY_COMPLETION_NODE = {
    "build_plan": ("understand_request", "workflow", "理解人才要求"),
    "filter_candidates": ("filter_candidates", "tool", "筛选候选人"),
    "validate_dimensions": ("build_dimensions", "workflow", "生成评估维度"),
    "finalize": (
        "parallel_assessment",
        "parallel",
        "检索证据并并行评估",
    ),
    "aggregate_candidates": (
        "score_candidates",
        "workflow",
        "证据质量门禁与排名",
    ),
    "validate_report": ("compose_report", "workflow", "合成可引用评估报告"),
}


def interrupt_payload(update: Any) -> dict[str, Any] | None:
    """Extract the public value from LangGraph's tuple[Interrupt, ...]."""

    if isinstance(update, dict):
        return update
    if isinstance(update, (tuple, list)) and update:
        value = getattr(update[0], "value", None)
        return value if isinstance(value, dict) else None
    return None


def step_for_start(node: str) -> tuple[str, str, str] | None:
    return STEP_BY_START_NODE.get(node)


def _selected_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {field: value[field] for field in fields if field in value}


def _candidate_summaries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    fields = (
        "candidate_id",
        "confirmed_score",
        "possible_score",
        "evidence_coverage_percent",
        "confidence",
    )
    return [_selected_fields(item, fields) for item in value if isinstance(item, dict)]


def _project_step_output(node: str, update: dict[str, Any]) -> dict[str, Any]:
    """Expose useful business output without leaking the full graph state."""

    output: dict[str, Any] = {"status": str(update.get("status", "completed"))}
    if node == "build_plan":
        output["talent_request"] = _selected_fields(
            update.get("talent_request"),
            (
                "task_type",
                "source",
                "target_job",
                "hard_conditions",
                "semantic_conditions",
                "evaluation_preferences",
            ),
        )
        output["query_plan"] = _selected_fields(
            update.get("query_plan"),
            (
                "task_type",
                "filters",
                "semantic_requirements",
                "preferences",
                "clarifications",
            ),
        )
    elif node == "filter_candidates":
        candidate_ids = update.get("candidate_ids")
        output["candidate_ids"] = candidate_ids if isinstance(candidate_ids, list) else []
        output["candidate_count"] = len(output["candidate_ids"])
    elif node in {"generate_dimensions", "validate_dimensions"}:
        dimensions = update.get("dimensions")
        if isinstance(dimensions, list):
            output["dimensions"] = dimensions
            output["dimension_count"] = len(dimensions)
        issues = update.get("validation_issues")
        if isinstance(issues, list):
            output["validation_issues"] = issues
    elif node == "aggregate_candidates":
        ranking = update.get("ranking")
        output["ranking"] = ranking if isinstance(ranking, list) else []
        output["candidates"] = _candidate_summaries(update.get("candidate_assessments"))
    elif node == "validate_report":
        report = update.get("report")
        output["report_chars"] = len(report) if isinstance(report, str) else 0
    return output


def _job_selection(payload: dict[str, Any]) -> dict[str, Any]:
    options = [
        {"value": str(item["job_code"]), "label": str(item["name"])}
        for item in payload.get("options", [])
        if isinstance(item, dict) and item.get("job_code") and item.get("name")
    ]
    return {
        "interaction_type": "job_selection",
        "title": "确认岗位",
        "description": str(payload.get("question", "请选择本次评估使用的岗位")),
        "options": options,
    }


def _requirement_revision(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "interaction_type": "requirement_revision",
        "interaction_id": str(payload.get("interaction_id", "")),
        "title": "修订人才要求",
        "description": str(
            payload.get("blocking_reason") or payload.get("question") or "当前要求需要修订"
        ),
        "input_kind": "text",
        "initial_value": str(payload.get("request_text", "")),
        "options": [],
    }


def project_graph_update(
    *,
    namespace: tuple[str, ...],
    node: str,
    update: Any,
) -> list[dict[str, Any]]:
    """Project graph updates into a stable protocol without leaking graph state."""

    del namespace
    if node == "__interrupt__":
        payload = interrupt_payload(update)
        if payload is None:
            return []
        interaction_type = payload.get("type")
        if interaction_type == "job_selection":
            data = _job_selection(payload)
        elif interaction_type == "requirement_revision":
            data = _requirement_revision(payload)
        else:
            data = {
                "interaction_type": str(interaction_type or "unknown"),
                "title": "需要人工确认",
                "description": str(payload.get("question", "请补充后继执行信息")),
                "options": [],
            }
        return [{"type": "hitl.required", "data": data}]

    if node == "generate_dimensions" and isinstance(update, dict):
        step_id, kind, title = STEP_BY_START_NODE[node]
        return [
            {
                "type": "step.progress",
                "data": {
                    "step_id": step_id,
                    "kind": kind,
                    "title": title,
                    "output": _project_step_output(node, update),
                },
            }
        ]

    step = STEP_BY_COMPLETION_NODE.get(node)
    if step is None or not isinstance(update, dict):
        return []
    step_id, kind, title = step
    return [
        {
            "type": "step.completed",
            "data": {
                "step_id": step_id,
                "kind": kind,
                "title": title,
                "summary": str(update.get("status", "completed")),
                "output": _project_step_output(node, update),
            },
        }
    ]
