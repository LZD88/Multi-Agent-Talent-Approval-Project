from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain.messages import AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.talent_decision_graph import DecisionContext


class TalentDecisionEngineInput(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    request_text: str


class TalentDecisionEngineOutput(TypedDict, total=False):
    messages: list[AnyMessage]
    revision_action: str
    revision_summary: str
    conversation_title: str
    status: str
    candidate_ids: list[str]
    ranking: list[str]
    report: str
    errors: list[str]


class TalentDecisionEngineState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    request_text: str
    revision_action: str
    revision_summary: str
    conversation_title: str
    input_mode: str
    job_matches: list[dict[str, Any]]
    selected_job: dict[str, Any] | None
    talent_request: dict[str, Any]
    query_plan: dict[str, Any]
    candidate_ids: list[str]
    condition_revisions: list[dict[str, Any]]
    plan_version: int
    clarifications: list[dict[str, str]]
    dimensions: list[dict[str, Any]]
    work_items: list[dict[str, Any]]
    branch_results: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]
    required_work_items: int
    work_item_limit: int
    evidence_items: list[dict[str, Any]]
    dimension_assessments: list[dict[str, Any]]
    consistency_issues: list[dict[str, Any]]
    candidate_assessments: list[dict[str, Any]]
    ranking: list[str]
    report_draft: dict[str, Any]
    report: str
    status: str
    errors: list[str]


def _route_after_request(
    state: TalentDecisionEngineState,
) -> Literal["evaluation", "stop"]:
    return "evaluation" if state.get("status") == "candidates_ready" else "stop"


def _route_after_evaluation(
    state: TalentDecisionEngineState,
) -> Literal["report", "stop"]:
    return (
        "report"
        if state.get("status") in {"branches_ready", "branches_ready_with_failures"}
        else "stop"
    )


def _begin_turn(state: TalentDecisionEngineState) -> dict[str, Any]:
    """返回局部更新：未返回的消息、当前输入、要求和标题均保留。"""

    update: dict[str, Any] = {
        "candidate_ids": [],
        "dimensions": [],
        "work_items": [],
        "branch_results": [],
        "evidence_items": [],
        "dimension_assessments": [],
        "consistency_issues": [],
        "candidate_assessments": [],
        "ranking": [],
        "report_draft": {},
        "report": "",
        "errors": [],
        "status": "received",
    }
    if not state.get("messages") and state.get("request_text"):
        # 兼容早期单轮脚本的纯文本输入，正常多轮 API 已经提交 HumanMessage。
        update["messages"] = [HumanMessage(content=state["request_text"])]
    return update


def build_talent_decision_engine(
    *,
    request_graph: Any,
    evaluation_graph: Any,
    report_graph: Any,
    checkpointer: Any | None = None,
):
    """构建人才决策引擎的主图构建入口"""
    builder = StateGraph(
        TalentDecisionEngineState,
        context_schema=DecisionContext,
        input_schema=TalentDecisionEngineInput,
        output_schema=TalentDecisionEngineOutput,
    )
    builder.add_node(
        "begin_turn",
        _begin_turn,
    )
    builder.add_node("request_subgraph", request_graph)
    builder.add_node("evaluation_subgraph", evaluation_graph)
    builder.add_node("report_subgraph", report_graph)
    builder.add_edge(START, "begin_turn")
    builder.add_edge("begin_turn", "request_subgraph")
    builder.add_conditional_edges(
        "request_subgraph",
        _route_after_request,
        {"evaluation": "evaluation_subgraph", "stop": END},
    )
    builder.add_conditional_edges(
        "evaluation_subgraph",
        _route_after_evaluation,
        {"report": "report_subgraph", "stop": END},
    )
    builder.add_edge("report_subgraph", END)
    return (
        builder.compile(checkpointer=checkpointer)
        if checkpointer is not None
        else builder.compile()
    )
