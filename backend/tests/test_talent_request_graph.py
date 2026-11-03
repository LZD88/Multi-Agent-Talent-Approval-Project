from __future__ import annotations

from langchain.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.query_plan import FilterCondition, SemanticRequirement
from app.talent_decision_graph import DecisionContext
from app.evaluation_case import EvaluationCaseDraft
from app.talent_request_graph import TalentRequestDraft, build_talent_request_graph


def _context() -> DecisionContext:
    return DecisionContext(tenant_id="course-demo", permission_scopes=("hr_private",))


def _job(
    code: str,
    name: str,
    *,
    match_type: str,
    score: float = 1.0,
    content: str = "负责企业知识库与 Agent 应用建设",
) -> dict:
    return {
        "job_code": code,
        "name": name,
        "match_score": score,
        "match_type": match_type,
        "version": 1,
        "content": content,
    }


class FixtureInterpreter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, request_text: str, selected_job: dict | None = None) -> TalentRequestDraft:
        selected_code = selected_job["job_code"] if selected_job else None
        self.calls.append((request_text, selected_code))
        if selected_job is not None:
            return TalentRequestDraft(
                input_mode="detailed_requirement",
                job_query=selected_job["name"],
                filters=[],
                semantic_requirements=[
                    SemanticRequirement(requirement_id="S1", query=selected_job["content"], required=True)
                ],
                evaluation_preferences=[],
                clarifications=[],
            )
        if "上海" in request_text:
            return TalentRequestDraft(
                input_mode="detailed_requirement",
                filters=[FilterCondition(field="region", operator="eq", value="上海")],
                semantic_requirements=[
                    SemanticRequirement(requirement_id="S1", query="企业知识库经验", required=True)
                ],
                evaluation_preferences=["有 Agent 评测经验优先"],
                clarifications=[],
            )
        return TalentRequestDraft(
            input_mode="job_name",
            job_query=request_text,
            filters=[],
            semantic_requirements=[],
            evaluation_preferences=[],
            clarifications=[],
        )


def test_detailed_requirement_builds_plan_without_job_lookup():
    interpreter = FixtureInterpreter()
    lookup_calls: list[str] = []

    graph = build_talent_request_graph(
        request_interpreter=interpreter,
        job_lookup=lambda query, context: lookup_calls.append(query) or [],
        checkpointer=InMemorySaver(),
    )
    result = graph.invoke(
        {"request_text": "筛选上海且有企业知识库经验的人才，有 Agent 评测经验优先"},
        {"configurable": {"thread_id": "lesson14-detailed"}},
        context=_context(),
    )

    assert result["status"] == "plan_ready"
    assert result["input_mode"] == "detailed_requirement"
    assert result["job_matches"] == []
    assert result["query_plan"]["filters"] == [
        {"field": "region", "operator": "eq", "value": "上海"}
    ]
    assert result["talent_request"]["evaluation_preferences"] == ["有 Agent 评测经验优先"]
    assert lookup_calls == []


def test_request_understanding_is_automatically_captured_from_graph_execution():
    captured: list[EvaluationCaseDraft] = []
    graph = build_talent_request_graph(
        request_interpreter=FixtureInterpreter(),
        job_lookup=lambda query, context: [],
        case_recorder=captured.append,
    )
    context = DecisionContext(
        tenant_id="course-demo",
        permission_scopes=("hr_private",),
        run_id="run-22",
        conversation_id="conversation-22",
        turn_no=3,
    )

    result = graph.invoke(
        {"request_text": "筛选上海且有企业知识库经验的人才"},
        context=context,
    )

    assert result["status"] == "plan_ready"
    assert len(captured) == 1
    case = captured[0]
    assert (case.source_run_id, case.conversation_id, case.turn_no) == (
        "run-22",
        "conversation-22",
        3,
    )
    assert case.input_payload["messages"][0]["content"] == "筛选上海且有企业知识库经验的人才"
    assert case.output_payload["draft"]["filters"][0]["value"] == "上海"
    assert case.status == "succeeded"


def test_unique_exact_job_match_continues_without_interrupt():
    interpreter = FixtureInterpreter()
    captured: list[EvaluationCaseDraft] = []
    graph = build_talent_request_graph(
        request_interpreter=interpreter,
        job_lookup=lambda query, context: [
            _job("JD-AI-001", "高级 AI 应用工程师", match_type="exact"),
            _job("JD-AI-002", "AI 平台工程师", match_type="fuzzy", score=0.61),
        ],
        checkpointer=InMemorySaver(),
        case_recorder=captured.append,
    )

    result = graph.invoke(
        {"request_text": "高级 AI 应用工程师"},
        {"configurable": {"thread_id": "lesson14-exact"}},
        context=DecisionContext(
            tenant_id="course-demo",
            permission_scopes=("hr_private",),
            run_id="run-exact-job",
            conversation_id="conversation-exact-job",
            turn_no=1,
        ),
    )

    assert result["status"] == "plan_ready"
    assert result["selected_job"]["job_code"] == "JD-AI-001"
    assert "__interrupt__" not in result
    assert interpreter.calls == [
        ("高级 AI 应用工程师", None),
        ("高级 AI 应用工程师", "JD-AI-001"),
    ]
    assert len(captured) == 3
    assert captured[1].output_payload["route"] == "select_exact_job"
    assert captured[0].input_payload["selected_job"] is None
    assert captured[2].input_payload["selected_job"]["job_code"] == "JD-AI-001"


def test_ambiguous_job_match_interrupts_and_resumes_same_thread():
    interpreter = FixtureInterpreter()
    lookup_calls: list[str] = []
    matches = [
        _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains"),
        _job("JD-AI-002", "AI 应用工程师（平台方向）", match_type="contains"),
    ]

    def lookup(query: str, context: DecisionContext) -> list[dict]:
        lookup_calls.append(query)
        return matches

    graph = build_talent_request_graph(
        request_interpreter=interpreter,
        job_lookup=lookup,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "lesson14-ambiguous"}}

    interrupted = graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())

    payload = interrupted["__interrupt__"][0].value
    assert payload["type"] == "job_selection"
    assert payload["request_text"] == "AI 应用工程师"
    assert [item["job_code"] for item in payload["options"]] == ["JD-AI-001", "JD-AI-002"]
    assert lookup_calls == ["AI 应用工程师"]

    resumed = graph.invoke(
        Command(resume={"action": "select", "job_code": "JD-AI-002"}),
        config,
        context=_context(),
    )

    assert resumed["status"] == "plan_ready"
    assert resumed["selected_job"]["job_code"] == "JD-AI-002"
    assert resumed["talent_request"]["target_job"]["job_code"] == "JD-AI-002"
    assert resumed["query_plan"]["semantic_requirements"][0]["query"] == "负责企业知识库与 Agent 应用建设"
    assert lookup_calls == ["AI 应用工程师"]


def test_ambiguous_job_match_can_be_cancelled():
    matches = [
        _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains"),
        _job("JD-AI-002", "AI 应用工程师（平台方向）", match_type="contains"),
    ]
    graph = build_talent_request_graph(
        request_interpreter=FixtureInterpreter(),
        job_lookup=lambda query, context: matches,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "lesson14-cancel-job"}}

    interrupted = graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())
    assert interrupted["__interrupt__"][0].value["actions"] == ["select", "cancel"]

    result = graph.invoke(Command(resume={"action": "cancel"}), config, context=_context())
    assert result["status"] == "cancelled"
    assert graph.get_state(config).next == ()

def test_no_job_match_ends_without_building_plan():
    graph = build_talent_request_graph(
        request_interpreter=FixtureInterpreter(),
        job_lookup=lambda query, context: [],
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        {"request_text": "量子招聘架构师"},
        {"configurable": {"thread_id": "lesson14-no-match"}},
        context=_context(),
    )

    assert result["status"] == "no_job_match"
    assert result["query_plan"] == {}
    assert result["clarifications"] == [
        {"expression": "量子招聘架构师", "reason": "未找到可确认的岗位 JD"}
    ]


def test_model_interpreter_prompt_distinguishes_region_from_current_position(monkeypatch):
    from app import talent_request_graph as module

    captured: dict[str, object] = {}

    class CapturingModel:
        def with_structured_output(self, schema, **kwargs):
            captured["schema"] = schema
            return self

        def invoke(self, messages):
            captured["messages"] = messages
            return {
                "draft": {
                    "input_mode": "detailed_requirement",
                    "filters": [{"field": "region", "operator": "eq", "value": "上海"}],
                    "semantic_requirements": [],
                    "evaluation_preferences": [],
                    "clarifications": [],
                },
                "revision_action": "initial",
                "revision_summary": "建立初始要求",
                "conversation_title": "上海人才筛选",
            }

    monkeypatch.setattr(module, "get_chat_model", lambda temperature=0: CapturingModel())

    result = module.model_request_interpreter(
        [HumanMessage(content="筛选地区在上海的候选人")],
        {},
    )

    system_prompt = captured["messages"][0][1]
    assert "结合最近消息窗口和 previous_request" in system_prompt
    assert "region: 工作地区" in system_prompt
    assert "current_position: 当前岗位" in system_prompt
    assert "地名只能映射为 region" in system_prompt
    assert result.draft.filters[0].field.value == "region"
