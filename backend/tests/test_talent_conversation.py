from __future__ import annotations

from typing import Any

from langchain.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.query_plan import FilterCondition, SemanticRequirement
from app.talent_decision_graph import DecisionContext
from app.talent_request_graph import (
    TalentRequestDraft,
    TalentRequestUnderstanding,
    build_talent_request_graph,
)


def _context() -> DecisionContext:
    return DecisionContext(tenant_id="course-demo", permission_scopes=("hr_private",))


class ConversationInterpreter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages, previous_request, selected_job=None, conversation_title=""):
        texts = [str(message.content) for message in messages]
        latest = texts[-1]
        self.calls.append({"messages": texts, "previous_request": previous_request})
        filters = list((previous_request or {}).get("hard_conditions", []))
        semantics = list((previous_request or {}).get("semantic_conditions", []))
        action, summary = "initial", "建立初始要求"
        if "Agent 评测" in latest and previous_request:
            action, summary = "add", "增加 Agent 评测经验"
            semantics.append({"requirement_id": "S2", "query": "Agent 评测经验", "required": True})
        elif "3 年" in latest:
            action, summary = "update", "工作年限改为 3 年以上"
            filters = [item for item in filters if item["field"] != "years_of_experience"]
            filters.append({"field": "years_of_experience", "operator": "gte", "value": 3})
        elif "删除" in latest:
            action, summary = "remove", "删除地区要求"
            filters = [item for item in filters if item["field"] != "region"]
        elif not previous_request:
            filters = [
                {"field": "region", "operator": "eq", "value": "上海"},
                {"field": "years_of_experience", "operator": "gte", "value": 5},
            ]
            semantics = [{"requirement_id": "S1", "query": "企业知识库经验", "required": True}]
        return TalentRequestUnderstanding(
            draft=TalentRequestDraft(
                input_mode="detailed_requirement",
                filters=[FilterCondition.model_validate(item) for item in filters],
                semantic_requirements=[SemanticRequirement.model_validate(item) for item in semantics],
            ),
            revision_action=action,
            revision_summary=summary,
            conversation_title=conversation_title or "上海知识库人才筛选",
        )


def test_same_thread_restores_messages_and_structured_request_for_add_update_remove():
    interpreter = ConversationInterpreter()
    graph = build_talent_request_graph(
        request_interpreter=interpreter,
        job_lookup=lambda query, context: [],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "conversation-1"}}

    first = graph.invoke({"messages": [HumanMessage(content="筛选上海地区、5 年以上且有企业知识库经验的人才")]}, config, context=_context())
    added = graph.invoke({"messages": [HumanMessage(content="再加一条，需要有 Agent 评测经验")]}, config, context=_context())
    updated = graph.invoke({"messages": [HumanMessage(content="工作年限改成 3 年以上")]}, config, context=_context())
    removed = graph.invoke({"messages": [HumanMessage(content="把地区要求删除")]}, config, context=_context())

    assert first["conversation_title"] == "上海知识库人才筛选"
    assert [added["revision_action"], updated["revision_action"], removed["revision_action"]] == ["add", "update", "remove"]
    assert removed["talent_request"]["hard_conditions"] == [
        {"field": "years_of_experience", "operator": "gte", "value": 3}
    ]
    assert interpreter.calls[-1]["messages"] == [
        "筛选上海地区、5 年以上且有企业知识库经验的人才",
        "再加一条，需要有 Agent 评测经验",
        "工作年限改成 3 年以上",
        "把地区要求删除",
    ]


def test_different_thread_has_isolated_message_history():
    interpreter = ConversationInterpreter()
    graph = build_talent_request_graph(request_interpreter=interpreter, job_lookup=lambda query, context: [], checkpointer=InMemorySaver())
    graph.invoke({"messages": [HumanMessage(content="筛选上海人才")]}, {"configurable": {"thread_id": "conversation-a"}}, context=_context())
    graph.invoke({"messages": [HumanMessage(content="筛选北京人才")]}, {"configurable": {"thread_id": "conversation-b"}}, context=_context())

    assert interpreter.calls[-1]["messages"] == ["筛选北京人才"]
    assert interpreter.calls[-1]["previous_request"] == {}


def test_request_interpreter_receives_current_turn_and_three_previous_turns():
    interpreter = ConversationInterpreter()
    graph = build_talent_request_graph(
        request_interpreter=interpreter,
        job_lookup=lambda query, context: [],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "conversation-window"}}

    for turn in range(1, 6):
        graph.invoke(
            {"messages": [HumanMessage(content=f"第 {turn} 轮要求")]},
            config,
            context=_context(),
        )

    assert interpreter.calls[-1]["messages"] == [
        "第 2 轮要求",
        "第 3 轮要求",
        "第 4 轮要求",
        "第 5 轮要求",
    ]
    assert len(graph.get_state(config).values["messages"]) == 5


def test_parent_passes_previous_request_and_preserves_title_across_four_turns():
    from app.talent_decision_engine import build_talent_decision_engine

    interpreter = ConversationInterpreter()
    titles_seen = []

    def interpret(messages, previous_request, selected_job, conversation_title):
        titles_seen.append(conversation_title)
        result = interpreter(messages, previous_request, selected_job, conversation_title)
        if conversation_title:
            result.conversation_title = "模型不应覆盖旧标题"
        return result

    graph = build_talent_decision_engine(
        request_graph=build_talent_request_graph(request_interpreter=interpret, job_lookup=lambda q, c: []),
        evaluation_graph=lambda state: {},
        report_graph=lambda state: {},
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "parent-conversation"}}
    for text in ["筛选上海地区、5 年以上且有企业知识库经验的人才", "增加 Agent 评测经验", "工作年限改成 3 年以上", "删除地区要求"]:
        graph.invoke({"messages": [HumanMessage(content=text)], "request_text": text}, config, context=_context())
    state = graph.get_state(config).values
    assert interpreter.calls[1]["previous_request"]["hard_conditions"][0]["value"] == "上海"
    assert titles_seen == ["", "上海知识库人才筛选", "上海知识库人才筛选", "上海知识库人才筛选"]
    assert state["conversation_title"] == "上海知识库人才筛选"
    assert len(state["messages"]) == 4
    assert state["talent_request"]["hard_conditions"] == [{"field": "years_of_experience", "operator": "gte", "value": 3}]
    assert len(state["talent_request"]["semantic_conditions"]) == 2


def test_parent_keeps_hitl_revision_message_for_following_turn():
    from langgraph.types import Command
    from app.talent_decision_engine import build_talent_decision_engine

    interpreter = ConversationInterpreter()
    graph = build_talent_decision_engine(
        request_graph=build_talent_request_graph(
            request_interpreter=interpreter,
            job_lookup=lambda q, c: [],
            candidate_filter=lambda filters, c: [] if any(f.value == 5 for f in filters) else ["C001"],
        ),
        evaluation_graph=lambda state: {}, report_graph=lambda state: {},
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "hitl-messages"}}
    graph.invoke({"messages": [HumanMessage(content="筛选上海地区、5 年以上且有企业知识库经验的人才")]}, config, context=_context())
    graph.invoke(Command(resume={"interaction_id": "rr-v0", "action": "revise", "revision_text": "工作年限改成 3 年以上"}), config, context=_context())
    assert len(graph.get_state(config).values["messages"]) == 2
    graph.invoke({"messages": [HumanMessage(content="增加 Agent 评测经验")]}, config, context=_context())
    assert interpreter.calls[-1]["messages"][-2:] == ["工作年限改成 3 年以上", "增加 Agent 评测经验"]


def test_begin_turn_clears_products_but_retains_conversation_state():
    from app.talent_decision_engine import _begin_turn, TalentDecisionEngineState
    from langgraph.graph import StateGraph, START, END

    builder = StateGraph(TalentDecisionEngineState)
    builder.add_node("begin_turn", _begin_turn)
    builder.add_edge(START, "begin_turn")
    builder.add_edge("begin_turn", END)
    result = builder.compile().invoke({
        "messages": [HumanMessage(content="旧消息"), HumanMessage(content="本轮消息")],
        "request_text": "本轮消息", "conversation_title": "原标题", "talent_request": {"hard_conditions": []},
        "report": "上轮报告", "ranking": ["C001"], "branch_results": [{"task_id": "old"}],
    })
    assert [m.content for m in result["messages"]] == ["旧消息", "本轮消息"]
    assert result["request_text"] == "本轮消息"
    assert result["conversation_title"] == "原标题"
    assert result["talent_request"] == {"hard_conditions": []}
    assert result["report"] == "" and result["ranking"] == [] and result["branch_results"] == []


def test_later_turn_does_not_ask_model_to_generate_title(monkeypatch):
    from app import talent_request_graph as module

    captured = {}

    class Model:
        def with_structured_output(self, schema, **kwargs):
            captured["schema"] = schema
            return self

        def invoke(self, messages):
            captured["prompt"] = messages
            return {"draft": {"input_mode": "detailed_requirement"}, "revision_action": "update", "revision_summary": "修改年限"}

    monkeypatch.setattr(module, "get_chat_model", lambda **kwargs: Model())
    result = module.model_request_interpreter(
        [HumanMessage(content=f"消息 {i}") for i in range(14)],
        {"hard_conditions": []},
        conversation_title="原会话标题",
    )
    assert "conversation_title" not in captured["schema"].model_fields
    assert result.conversation_title == "原会话标题"
    prompt = captured["prompt"][1][1]
    assert "human: 消息 9\n" not in prompt
    assert "human: 消息 10\n" in prompt
    assert "human: 消息 13\n" in prompt
