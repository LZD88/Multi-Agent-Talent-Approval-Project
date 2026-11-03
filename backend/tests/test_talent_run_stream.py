from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from app.talent_decision_graph import DecisionContext
from app.talent_run_stream import RunStatus, TalentRunManager


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StreamingState(TypedDict, total=False):
    request_text: str
    selected_job: str
    status: str
    candidate_ids: list[str]
    dimensions: list[dict[str, Any]]
    work_items: list[dict[str, Any]]
    branch_results: list[dict[str, Any]]
    evidence_items: list[dict[str, Any]]
    ranking: list[str]
    report: str
    errors: list[str]


def _streaming_graph(*, require_job_selection: bool = False, slow: bool = False):
    builder = StateGraph(StreamingState)

    async def interpret_input(state: StreamingState) -> dict[str, Any]:
        if slow:
            await asyncio.sleep(1)
        return {"status": "request_understood"}

    def confirm_job(_: StreamingState) -> dict[str, Any]:
        decision = interrupt(
            {
                "type": "job_selection",
                "question": "请选择岗位",
                "options": [
                    {"job_code": "JD-AI-001", "name": "高级 AI 应用工程师"},
                    {"job_code": "JD-AI-002", "name": "AI 应用工程师（平台方向）"},
                ],
            }
        )
        return {"selected_job": decision["job_code"]}

    def filter_candidates(_: StreamingState) -> dict[str, Any]:
        return {"candidate_ids": ["C001"], "status": "candidates_ready"}

    def generate_dimensions(_: StreamingState) -> dict[str, Any]:
        return {"dimensions": [{"name": "RAG 落地"}], "status": "dimensions_generated"}

    def prepare_work_items(_: StreamingState) -> dict[str, Any]:
        return {
            "work_items": [{"task_id": "C001:1"}],
            "required_work_items": 1,
            "status": "work_items_ready",
        }

    def run_assessment_branch(_: StreamingState) -> dict[str, Any]:
        return {
            "branch_results": [{"task_id": "C001:1", "execution_status": "succeeded"}],
            "status": "branches_ready",
        }

    def receive_input(_: StreamingState) -> dict[str, Any]:
        return {
            "evidence_items": [
                {
                    "model_ref": "E1",
                    "candidate_id": "C001",
                    "source_label": "C001_项目复盘",
                    "quote": "完成企业知识库检索质量评测",
                    "citation_url": "/api/evidence/citations/chunk-1",
                }
            ]
        }

    def aggregate_candidates(_: StreamingState) -> dict[str, Any]:
        return {"ranking": ["C001"], "status": "candidates_ranked"}

    def validate_report(state: StreamingState) -> dict[str, Any]:
        return {
            "report": f"# {state['request_text']}\n\nC001 匹配 [E1](/api/evidence/citations/chunk-1)",
            "status": "completed",
        }

    builder.add_node("interpret_input", interpret_input)
    if require_job_selection:
        builder.add_node("confirm_job", confirm_job)
    builder.add_node("filter_candidates", filter_candidates)
    builder.add_node("generate_dimensions", generate_dimensions)
    builder.add_node("prepare_work_items", prepare_work_items)
    builder.add_node("run_assessment_branch", run_assessment_branch)
    builder.add_node("receive_input", receive_input)
    builder.add_node("aggregate_candidates", aggregate_candidates)
    builder.add_node("validate_report", validate_report)
    builder.add_edge(START, "interpret_input")
    if require_job_selection:
        builder.add_edge("interpret_input", "confirm_job")
        builder.add_edge("confirm_job", "filter_candidates")
    else:
        builder.add_edge("interpret_input", "filter_candidates")
    builder.add_edge("filter_candidates", "generate_dimensions")
    builder.add_edge("generate_dimensions", "prepare_work_items")
    builder.add_edge("prepare_work_items", "run_assessment_branch")
    builder.add_edge("run_assessment_branch", "receive_input")
    builder.add_edge("receive_input", "aggregate_candidates")
    builder.add_edge("aggregate_candidates", "validate_report")
    builder.add_edge("validate_report", END)
    return builder.compile(checkpointer=InMemorySaver())


def _manager(graph=None) -> TalentRunManager:
    return TalentRunManager(
        graph=graph or _streaming_graph(),
        context=DecisionContext(
            tenant_id="course-demo",
            permission_scopes=("hr_private",),
        ),
        max_concurrency=4,
    )


def _capacity_exceeded_graph():
    builder = StateGraph(StreamingState)

    def prepare_work_items(_: StreamingState) -> dict[str, Any]:
        return {
            "required_work_items": 30,
            "status": "capacity_exceeded",
            "errors": ["评估任务数 30 超过本次上限 24"],
        }

    builder.add_node("prepare_work_items", prepare_work_items)
    builder.add_edge(START, "prepare_work_items")
    builder.add_edge("prepare_work_items", END)
    return builder.compile()


def _context_capture_graph(captured: list[DecisionContext]):
    builder = StateGraph(StreamingState, context_schema=DecisionContext)

    def capture_context(
        _: StreamingState,
        runtime,
    ) -> dict[str, Any]:
        captured.append(runtime.context)
        return {"status": "completed"}

    builder.add_node("capture_context", capture_context)
    builder.add_edge(START, "capture_context")
    builder.add_edge("capture_context", END)
    return builder.compile()


async def _wait_for_status(
    manager: TalentRunManager,
    run_id: str,
    expected: RunStatus,
    *,
    attempts: int = 200,
) -> None:
    for _ in range(attempts):
        if manager.get_run(run_id).status == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {expected}")


@pytest.mark.anyio
async def test_run_projects_compiled_graph_output_instead_of_fixture_report():
    manager = _manager()
    question = "地区为上海且有 Agent 评测经验"

    run = await manager.create_run(question)
    await _wait_for_status(manager, run.run_id, RunStatus.COMPLETED)

    events = manager.events_since(run.run_id, cursor=0)
    report = next(event for event in events if event.type == "report.completed")
    assert question in report.data["markdown"]
    assert report.data["citations"] == [
        {
            "ref": "E1",
            "candidate": "C001",
            "document": "C001_项目复盘",
            "quote": "完成企业知识库检索质量评测",
            "url": "/api/evidence/citations/chunk-1",
        }
    ]
    assert "林晓" not in report.data["markdown"]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].type == "run.completed"


@pytest.mark.anyio
async def test_each_run_injects_its_identity_into_graph_runtime_context():
    captured: list[DecisionContext] = []
    manager = _manager(_context_capture_graph(captured))

    first = await manager.create_run("第一轮")
    await _wait_for_status(manager, first.run_id, RunStatus.COMPLETED)
    second = await manager.create_run("第二轮", conversation_id=first.conversation_id)
    await _wait_for_status(manager, second.run_id, RunStatus.COMPLETED)

    assert [(item.run_id, item.conversation_id, item.turn_no) for item in captured] == [
        (first.run_id, first.conversation_id, 1),
        (second.run_id, second.conversation_id, 2),
    ]


@pytest.mark.anyio
async def test_conversation_update_is_projected_as_a_public_run_event():
    builder = StateGraph(dict)
    builder.add_node(
        "interpret_input",
        lambda state: {
            "conversation_title": "3 年以上经验人才",
            "revision_action": "update",
            "revision_summary": "工作年限改为 3 年",
            "status": "completed",
        },
    )
    builder.add_edge(START, "interpret_input")
    builder.add_edge("interpret_input", END)
    manager = _manager(builder.compile())

    run = await manager.create_run("工作年限改成 3 年以上")
    await _wait_for_status(manager, run.run_id, RunStatus.COMPLETED)

    title_event = next(
        item
        for item in manager.events_since(run.run_id, 0)
        if item.type == "conversation.updated"
    )
    revision_event = next(
        item
        for item in manager.events_since(run.run_id, 0)
        if item.type == "requirement.revised"
    )
    assert title_event.data == {"title": "3 年以上经验人才"}
    assert revision_event.data == {
        "action": "update",
        "summary": "工作年限改为 3 年",
    }


@pytest.mark.anyio
async def test_capacity_exceeded_marks_run_and_active_step_failed():
    manager = _manager(_capacity_exceeded_graph())

    run = await manager.create_run("评估 AI 应用工程师候选人")
    await _wait_for_status(manager, run.run_id, RunStatus.FAILED)

    events = manager.events_since(run.run_id, cursor=0)
    assert events[-1].type == "run.failed"
    assert events[-1].data == {
        "message": "评估任务数 30 超过本次上限 24",
        "graph_status": "capacity_exceeded",
    }


@pytest.mark.anyio
async def test_two_new_turns_continue_numbering_after_process_restart():
    class ExistingHistory:
        last_turn = 4

        def next_turn_no(self, conversation_id):
            return self.last_turn + 1

        def record(self, event):
            if event.type == "run.started":
                self.last_turn = event.data["turn_no"]

    manager = TalentRunManager(graph=_streaming_graph(), record_store=ExistingHistory())
    fifth = await manager.create_run("增加经验要求", conversation_id="existing")
    await _wait_for_status(manager, fifth.run_id, RunStatus.COMPLETED)
    sixth = await manager.create_run("修改年限要求", conversation_id="existing")
    await _wait_for_status(manager, sixth.run_id, RunStatus.COMPLETED)
    assert [fifth.turn_no, sixth.turn_no] == [5, 6]


@pytest.mark.anyio
async def test_actual_langgraph_interrupt_resumes_same_thread_and_run():
    manager = _manager(_streaming_graph(require_job_selection=True))
    run = await manager.create_run("AI 应用工程师")
    await _wait_for_status(manager, run.run_id, RunStatus.WAITING_INPUT)

    interrupt_event = manager.events_since(run.run_id, 0)[-1]
    assert interrupt_event.type == "hitl.required"
    assert interrupt_event.data["interaction_type"] == "job_selection"
    assert interrupt_event.data["options"][0] == {
        "value": "JD-AI-001",
        "label": "高级 AI 应用工程师",
    }
    assert manager.is_stream_finished(run.run_id) is False

    await manager.resume_run(run.run_id, "JD-AI-001")
    await _wait_for_status(manager, run.run_id, RunStatus.COMPLETED)

    after_resume = manager.events_since(run.run_id, interrupt_event.sequence)
    assert after_resume[0].type == "hitl.resolved"
    assert after_resume[-1].type == "run.completed"
    assert all(event.run_id == run.run_id for event in after_resume)
    assert manager.is_stream_finished(run.run_id) is True


@pytest.mark.anyio
async def test_cancel_marks_run_cancelled_and_is_idempotent():
    manager = _manager(_streaming_graph(slow=True))
    run = await manager.create_run("地区为上海")

    await manager.cancel_run(run.run_id)
    await manager.cancel_run(run.run_id)

    events = manager.events_since(run.run_id, cursor=0)
    assert manager.get_run(run.run_id).status == RunStatus.CANCELLED
    assert [event.type for event in events].count("run.cancelled") == 1
    assert events[-1].type == "run.cancelled"
