from __future__ import annotations

from typing import Any
import json
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.talent_decision_engine import build_talent_decision_engine
from app.talent_decision_graph import DecisionContext


def _single_node_graph(update: dict[str, Any]):
    builder = StateGraph(dict)
    builder.add_node("work", lambda _: update)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    return builder.compile()


def test_decision_engine_maps_confirmed_candidates_into_evaluation_subgraph():
    request_graph = _single_node_graph(
        {
            "status": "candidates_ready",
            "talent_request": {"semantic_conditions": ["AI 项目"]},
            "query_plan": {"filters": []},
            "candidate_ids": ["C001", "C002"],
        }
    )

    def evaluation_node(state: dict[str, Any]) -> dict[str, Any]:
        assert state["candidate_ids"] == ["C001", "C002"]
        return {
            "status": "branches_ready",
            "dimensions": [{"name": "AI 经验"}],
            "branch_results": [{"task_id": "C001:1"}],
        }

    evaluation_builder = StateGraph(dict)
    evaluation_builder.add_node("work", evaluation_node)
    evaluation_builder.add_edge(START, "work")
    evaluation_builder.add_edge("work", END)
    evaluation_graph = evaluation_builder.compile()
    report_graph = _single_node_graph(
        {
            "status": "completed",
            "ranking": ["C001", "C002"],
            "report": "# 人才评估报告",
        }
    )
    graph = build_talent_decision_engine(
        request_graph=request_graph,
        evaluation_graph=evaluation_graph,
        report_graph=report_graph,
    )

    result = graph.invoke(
        {"request_text": "筛选有 AI 项目经验的候选人"},
        context=DecisionContext(tenant_id="course-demo", permission_scopes=("hr_private",)),
    )

    assert result["status"] == "completed"
    assert result["candidate_ids"] == ["C001", "C002"]
    assert result["ranking"] == ["C001", "C002"]
    assert result["report"] == "# 人才评估报告"


def test_decision_engine_stops_when_request_subgraph_is_cancelled():
    request_graph = _single_node_graph({"status": "cancelled"})
    evaluation_graph = _single_node_graph({"status": "should_not_run"})
    report_graph = _single_node_graph({"status": "should_not_run"})
    graph = build_talent_decision_engine(
        request_graph=request_graph,
        evaluation_graph=evaluation_graph,
        report_graph=report_graph,
    )

    result = graph.invoke(
        {"request_text": "AI 应用工程师"},
        context=DecisionContext(tenant_id="course-demo", permission_scopes=("hr_private",)),
    )

    assert result["status"] == "cancelled"


def test_decision_engine_accepts_parent_checkpointer_for_real_interrupt_resume():
    request_builder = StateGraph(dict)

    def choose_job(_: dict[str, Any]) -> dict[str, Any]:
        decision = interrupt({"type": "job_selection"})
        return {
            "status": "candidates_ready",
            "candidate_ids": [decision["job_code"]],
        }

    request_builder.add_node("choose_job", choose_job)
    request_builder.add_edge(START, "choose_job")
    request_builder.add_edge("choose_job", END)
    graph = build_talent_decision_engine(
        request_graph=request_builder.compile(),
        evaluation_graph=_single_node_graph({"status": "stopped"}),
        report_graph=_single_node_graph({"status": "stopped"}),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "engine-hitl"}}
    context = DecisionContext(
        tenant_id="course-demo",
        permission_scopes=("hr_private",),
    )

    paused = graph.invoke(
        {"request_text": "AI 应用工程师"},
        config,
        context=context,
    )
    assert paused["__interrupt__"]

    resumed = graph.invoke(Command(resume={"job_code": "JD-AI-001"}), config, context=context)
    assert resumed["candidate_ids"] == ["JD-AI-001"]


def test_langgraph_registers_integrated_talent_decision_engine():
    config = json.loads((Path(__file__).parents[1] / "langgraph.json").read_text())

    assert config["graphs"]["talent_decision_engine"] == (
        "./app/talent_decision_runtime.py:graph"
    )


def test_event_demo_prints_parent_and_child_nodes_in_execution_order():
    from scripts.verify_talent_decision_events import collect_demo_events

    events = collect_demo_events()

    assert [(item["scope"], item["node"]) for item in events] == [
        ("main", "begin_turn"),
        ("request_subgraph", "receive_request"),
        ("request_subgraph", "compile_request"),
        ("request_subgraph", "filter_candidates"),
        ("main", "request_subgraph"),
        ("evaluation_subgraph", "generate_dimensions"),
        ("evaluation_subgraph", "evaluate_candidates"),
        ("main", "evaluation_subgraph"),
        ("report_subgraph", "score_candidates"),
        ("report_subgraph", "compose_report"),
        ("main", "report_subgraph"),
    ]
    assert events[3]["update"]["status"] == "candidates_ready"
    assert events[6]["update"]["status"] == "branches_ready"
    assert events[9]["update"]["ranking"] == ["C001", "C002"]
    assert events[-1]["update"]["status"] == "completed"
