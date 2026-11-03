from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.talent_run_history import SqlTalentRunHistoryStore
from app.talent_run_stream import RunEvent


def _store() -> SqlTalentRunHistoryStore:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return SqlTalentRunHistoryStore(sessionmaker(bind=engine, expire_on_commit=False))


def _event(sequence: int, event_type: str, data: dict, timestamp: str) -> RunEvent:
    return RunEvent(
        event_id=f"run-1:{sequence}",
        sequence=sequence,
        type=event_type,
        run_id="run-1",
        timestamp=timestamp,
        data=data,
    )


def test_completed_run_is_rebuilt_from_run_and_step_snapshots():
    store = _store()
    events = [
        _event(1, "run.started", {"message": "筛选上海的 AI 工程师"}, "2026-10-11T01:00:00+00:00"),
        _event(
            2,
            "step.started",
            {"step_id": "filter_candidates", "kind": "tool", "title": "筛选候选人"},
            "2026-10-11T01:00:01+00:00",
        ),
        _event(
            3,
            "step.progress",
            {
                "step_id": "filter_candidates",
                "title": "筛选候选人",
                "completed": 1,
                "total": 1,
                "output": {"candidate_count": 1},
            },
            "2026-10-11T01:00:02+00:00",
        ),
        _event(
            4,
            "step.completed",
            {
                "step_id": "filter_candidates",
                "kind": "tool",
                "title": "筛选候选人",
                "summary": "命中 1 名候选人",
                "output": {"status": "candidates_ready"},
            },
            "2026-10-11T01:00:03.500000+00:00",
        ),
        _event(
            5,
            "report.completed",
            {
                "markdown": "# 候选人评估报告",
                "citations": [{"ref": "E1", "quote": "证据原文"}],
                "trace_id": "trace-1",
            },
            "2026-10-11T01:00:04+00:00",
        ),
        _event(6, "run.completed", {"graph_status": "completed"}, "2026-10-11T01:00:05+00:00"),
    ]

    for event in events:
        store.record(event)

    history = store.list_runs(limit=20)
    detail = store.get_run("run-1")

    assert len(history) == 1
    assert history[0]["message"] == "筛选上海的 AI 工程师"
    assert history[0]["status"] == "completed"
    assert detail is not None
    assert detail["report"] == "# 候选人评估报告"
    assert detail["trace_id"] == "trace-1"
    assert detail["citations"] == [{"ref": "E1", "quote": "证据原文"}]
    assert detail["steps"] == [
        {
            "step_id": "filter_candidates",
            "position": 1,
            "title": "筛选候选人",
            "kind": "tool",
            "status": "completed",
            "completed": 1,
            "total": 1,
            "summary": "命中 1 名候选人",
            "output": {"candidate_count": 1, "status": "candidates_ready"},
            "started_at": "2026-10-11T01:00:01",
            "completed_at": "2026-10-11T01:00:03.500000",
            "duration_ms": 2500,
        }
    ]


def test_history_limit_keeps_newest_runs_first():
    store = _store()
    store.record(_event(1, "run.started", {"message": "第一次"}, "2026-10-11T01:00:00+00:00"))
    store.record(
        RunEvent(
            event_id="run-2:1",
            sequence=1,
            type="run.started",
            run_id="run-2",
            timestamp="2026-10-11T02:00:00+00:00",
            data={"message": "第二次"},
        )
    )

    history = store.list_runs(limit=1)

    assert [item["run_id"] for item in history] == ["run-2"]


def test_conversation_projection_keeps_title_status_updated_at_and_turn_order():
    store = _store()
    events = [
        RunEvent(
            event_id="run-1:1",
            sequence=1,
            type="run.started",
            run_id="run-1",
            timestamp="2026-10-11T01:00:00+00:00",
            data={
                "message": "筛选上海地区、5 年以上经验的候选人",
                "conversation_id": "conversation-1",
                "turn_no": 1,
            },
        ),
        RunEvent(
            event_id="run-1:2",
            sequence=2,
            type="conversation.updated",
            run_id="run-1",
            timestamp="2026-10-11T01:00:01+00:00",
            data={"title": "上海资深人才筛选"},
        ),
        RunEvent(
            event_id="run-1:3",
            sequence=3,
            type="step.started",
            run_id="run-1",
            timestamp="2026-10-11T01:00:02+00:00",
            data={"step_id": "understand_request", "title": "理解人才要求"},
        ),
        RunEvent(
            event_id="run-1:4",
            sequence=4,
            type="step.completed",
            run_id="run-1",
            timestamp="2026-10-11T01:00:03+00:00",
            data={
                "step_id": "understand_request",
                "title": "理解人才要求",
                "summary": "已得到首轮要求",
            },
        ),
        RunEvent(
            event_id="run-1:5",
            sequence=5,
            type="report.completed",
            run_id="run-1",
            timestamp="2026-10-11T01:00:04+00:00",
            data={
                "markdown": "# 首轮报告",
                "citations": [{"ref": "E1", "quote": "首轮证据"}],
                "trace_id": "trace-run-1",
            },
        ),
        RunEvent(
            event_id="run-1:6",
            sequence=6,
            type="run.completed",
            run_id="run-1",
            timestamp="2026-10-11T01:00:05+00:00",
            data={"graph_status": "completed"},
        ),
        RunEvent(
            event_id="run-2:1",
            sequence=1,
            type="run.started",
            run_id="run-2",
            timestamp="2026-10-11T02:00:00+00:00",
            data={
                "message": "地区要求删除",
                "conversation_id": "conversation-1",
                "turn_no": 2,
            },
        ),
        RunEvent(
            event_id="run-2:2",
            sequence=2,
            type="conversation.updated",
            run_id="run-2",
            timestamp="2026-10-11T02:00:01+00:00",
            data={"title": "上海资深人才筛选"},
        ),
        RunEvent(
            event_id="run-2:3",
            sequence=3,
            type="run.completed",
            run_id="run-2",
            timestamp="2026-10-11T02:00:02+00:00",
            data={"graph_status": "completed"},
        ),
    ]
    for event in events:
        store.record(event)

    detail = store.get_run("run-2")
    conversations = store.list_conversations(status="completed")

    assert detail is not None
    assert detail["conversation_id"] == "conversation-1"
    assert detail["turn_no"] == 2
    assert detail["conversation_turns"] == [
        {
            "run_id": "run-1",
            "turn_no": 1,
            "message": "筛选上海地区、5 年以上经验的候选人",
            "status": "completed",
            "report": "# 首轮报告",
            "citations": [{"ref": "E1", "quote": "首轮证据"}],
            "trace_id": "trace-run-1",
            "error": "",
            "steps": [
                {
                    "step_id": "understand_request",
                    "position": 1,
                    "title": "理解人才要求",
                    "kind": None,
                    "status": "completed",
                    "completed": None,
                    "total": None,
                    "summary": "已得到首轮要求",
                    "output": {},
                    "started_at": "2026-10-11T01:00:02",
                    "completed_at": "2026-10-11T01:00:03",
                    "duration_ms": 1000,
                }
            ],
        },
        {
            "run_id": "run-2",
            "turn_no": 2,
            "message": "地区要求删除",
            "status": "completed",
            "report": "",
            "citations": [],
            "trace_id": "",
            "error": "",
            "steps": [],
        },
    ]
    assert conversations == [{
        "conversation_id": "conversation-1",
        "title": "上海资深人才筛选",
        "status": "completed",
        "updated_at": "2026-10-11T02:00:02",
        "latest_run_id": "run-2",
    }]
    assert store.next_turn_no("conversation-1") == 3


def test_human_waiting_time_is_excluded_from_step_duration():
    store = _store()
    events = [
        _event(1, "run.started", {"message": "AI 应用工程师"}, "2026-10-11T01:00:00+00:00"),
        _event(
            2,
            "step.started",
            {"step_id": "understand_request", "title": "理解人才要求"},
            "2026-10-11T01:00:01+00:00",
        ),
        _event(3, "hitl.required", {"title": "确认岗位"}, "2026-10-11T01:00:03+00:00"),
        _event(4, "hitl.resolved", {}, "2026-10-11T01:01:03+00:00"),
        _event(
            5,
            "step.completed",
            {"step_id": "understand_request", "title": "理解人才要求"},
            "2026-10-11T01:01:04.500000+00:00",
        ),
    ]

    for event in events:
        store.record(event)

    detail = store.get_run("run-1")

    assert detail is not None
    assert detail["steps"][0]["duration_ms"] == 3500

