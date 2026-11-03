from langgraph.types import Interrupt

from app.talent_event_projection import project_graph_update


def test_actual_request_node_projects_only_safe_business_fields():
    projected = project_graph_update(
        namespace=("request_subgraph:task-1",),
        node="build_plan",
        update={
            "status": "plan_ready",
            "talent_request": {
                "original_text": "sensitive raw request",
                "task_type": "evaluate_and_recommend",
                "source": "natural_language",
                "target_job": None,
                "hard_conditions": [{"field": "region", "operator": "eq", "value": "上海"}],
                "semantic_conditions": [{"text": "Agent 评测"}],
                "evaluation_preferences": {"top_k": 5},
            },
            "query_plan": {
                "task_type": "find_talent",
                "filters": [{"field": "region", "operator": "eq", "value": "上海"}],
                "semantic_requirements": [{"text": "Agent 评测"}],
                "preferences": {"top_k": 5},
                "clarifications": [],
            },
        },
    )

    assert projected == [
        {
            "type": "step.completed",
            "data": {
                "step_id": "understand_request",
                "kind": "workflow",
                "title": "理解人才要求",
                "summary": "plan_ready",
                "output": {
                    "status": "plan_ready",
                    "talent_request": {
                        "task_type": "evaluate_and_recommend",
                        "source": "natural_language",
                        "target_job": None,
                        "hard_conditions": [
                            {"field": "region", "operator": "eq", "value": "上海"}
                        ],
                        "semantic_conditions": [{"text": "Agent 评测"}],
                        "evaluation_preferences": {"top_k": 5},
                    },
                    "query_plan": {
                        "task_type": "find_talent",
                        "filters": [
                            {"field": "region", "operator": "eq", "value": "上海"}
                        ],
                        "semantic_requirements": [{"text": "Agent 评测"}],
                        "preferences": {"top_k": 5},
                        "clarifications": [],
                    },
                },
            },
        }
    ]
    assert "sensitive raw request" not in str(projected)


def test_langgraph_interrupt_object_projects_job_options():
    projected = project_graph_update(
        namespace=("request_subgraph:task-1",),
        node="__interrupt__",
        update=(
            Interrupt(
                value={
                    "type": "job_selection",
                    "question": "请选择本次人才评估使用的岗位 JD",
                    "options": [
                        {"job_code": "JD-AI-001", "name": "高级 AI 应用工程师"}
                    ],
                },
                id="interrupt-1",
            ),
        ),
    )

    assert projected == [
        {
            "type": "hitl.required",
            "data": {
                "interaction_type": "job_selection",
                "title": "确认岗位",
                "description": "请选择本次人才评估使用的岗位 JD",
                "options": [
                    {"value": "JD-AI-001", "label": "高级 AI 应用工程师"}
                ],
            },
        }
    ]


def test_requirement_revision_interrupt_keeps_real_reason_and_editable_text():
    projected = project_graph_update(
        namespace=("request_subgraph:task-1",),
        node="__interrupt__",
        update=(
            Interrupt(
                value={
                    "type": "requirement_revision",
                    "interaction_id": "rr-v0",
                    "blocking_reason": "地区条件被错误解析",
                    "question": "请修订人才要求",
                    "request_text": "筛选上海候选人",
                },
                id="interrupt-2",
            ),
        ),
    )

    assert projected[0]["data"] == {
        "interaction_type": "requirement_revision",
        "interaction_id": "rr-v0",
        "title": "修订人才要求",
        "description": "地区条件被错误解析",
        "input_kind": "text",
        "initial_value": "筛选上海候选人",
        "options": [],
    }


def test_dimension_generation_projects_output_before_validation_completes():
    projected = project_graph_update(
        namespace=("evaluation_subgraph:task-1",),
        node="generate_dimensions",
        update={
            "status": "dimensions_generated",
            "dimensions": [
                {"name": "企业知识库", "weight_percent": 50},
                {"name": "Agent 评测", "weight_percent": 50},
            ],
        },
    )

    assert projected == [
        {
            "type": "step.progress",
            "data": {
                "step_id": "build_dimensions",
                "kind": "workflow",
                "title": "生成评估维度",
                "output": {
                    "status": "dimensions_generated",
                    "dimensions": [
                        {"name": "企业知识库", "weight_percent": 50},
                        {"name": "Agent 评测", "weight_percent": 50},
                    ],
                    "dimension_count": 2,
                },
            },
        }
    ]
