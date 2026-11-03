from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.main import create_app
from app.talent_decision_graph import DecisionContext
from app.models import Base
from app.talent_run_history import SqlTalentRunHistoryStore
from app.talent_run_stream import RunEvent, TalentRunManager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _job_selection_manager() -> TalentRunManager:
    builder = StateGraph(dict)

    def confirm_job(_: dict):
        decision = interrupt(
            {
                "type": "job_selection",
                "question": "请选择岗位",
                "options": [
                    {"job_code": "JD-AI-001", "name": "高级 AI 应用工程师"}
                ],
            }
        )
        return {"selected": decision["job_code"]}

    builder.add_node("confirm_job", confirm_job)
    builder.add_edge(START, "confirm_job")
    builder.add_edge("confirm_job", END)
    return TalentRunManager(
        graph=builder.compile(checkpointer=InMemorySaver()),
        context=DecisionContext(
            tenant_id="course-demo",
            permission_scopes=("hr_private",),
        ),
    )


def _history_manager() -> TalentRunManager:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    store = SqlTalentRunHistoryStore(sessionmaker(bind=engine, expire_on_commit=False))
    store.record(
        RunEvent(
            event_id="saved-run:1",
            sequence=1,
            type="run.started",
            run_id="saved-run",
            timestamp="2026-10-11T01:00:00+00:00",
            data={"message": "筛选企业知识库项目候选人"},
        )
    )
    store.record(
        RunEvent(
            event_id="saved-run:2",
            sequence=2,
            type="run.completed",
            run_id="saved-run",
            timestamp="2026-10-11T01:00:05+00:00",
            data={"graph_status": "completed"},
        )
    )
    return TalentRunManager(
        graph=_job_selection_manager()._get_graph(),
        context=DecisionContext(tenant_id="course-demo", permission_scopes=("hr_private",)),
        record_store=store,
    )


def _completed_manager() -> TalentRunManager:
    builder = StateGraph(dict)
    builder.add_node("complete", lambda _: {"status": "completed"})
    builder.add_edge(START, "complete")
    builder.add_edge("complete", END)
    return TalentRunManager(
        graph=builder.compile(checkpointer=InMemorySaver()),
        context=DecisionContext(
            tenant_id="course-demo",
            permission_scopes=("hr_private",),
        ),
    )


def test_sse_frame_uses_protocol_id_type_and_json_envelope():
    from app.talent_run_api import encode_sse

    event = RunEvent(
        event_id="run-1:7",
        sequence=7,
        type="step.completed",
        run_id="run-1",
        timestamp="2026-09-27T10:00:00+00:00",
        data={"title": "理解人才要求"},
    )

    frame = encode_sse(event)

    assert frame.startswith("id: run-1:7\nevent: step.completed\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload["sequence"] == 7
    assert payload["schema_version"] == "1.0"


def test_sse_endpoint_does_not_expose_replay_cursor_parameters():
    app = create_app(talent_run_manager=_job_selection_manager())

    operation = app.openapi()["paths"]["/api/runs/{run_id}/events"]["get"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}

    assert "after_sequence" not in parameter_names
    assert "last-event-id" not in {name.lower() for name in parameter_names}


def test_create_run_returns_stream_location_and_cancel_is_idempotent():
    app = create_app(talent_run_manager=_job_selection_manager())
    with TestClient(app) as client:
        created = client.post(
            "/api/runs",
            json={"message": "筛选有企业知识库经验的候选人"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["events_url"] == f"/api/runs/{body['run_id']}/events"

        cancelled = client.delete(f"/api/runs/{body['run_id']}")
        repeated = client.delete(f"/api/runs/{body['run_id']}")

        assert cancelled.status_code == 200
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "cancelled"


def test_follow_up_message_reuses_conversation_and_creates_a_new_run():
    manager = _completed_manager()
    app = create_app(talent_run_manager=manager)
    with TestClient(app) as client:
        first = client.post("/api/runs", json={"message": "筛选上海地区候选人"})
        first_body = first.json()
        for _ in range(100):
            if manager.get_run(first_body["run_id"]).status == "completed":
                break
            time.sleep(0.01)

        second = client.post(
            "/api/runs",
            json={
                "message": "地区要求删除",
                "conversation_id": first_body["conversation_id"],
            },
        )

    second_body = second.json()
    assert first_body["conversation_id"] == second_body["conversation_id"]
    assert first_body["run_id"] != second_body["run_id"]
    assert first_body["turn_no"] == 1
    assert second_body["turn_no"] == 2


def test_resume_rejects_unknown_choice():
    app = create_app(talent_run_manager=_job_selection_manager())
    with TestClient(app) as client:
        created = client.post(
            "/api/runs",
            json={"message": "高级 AI 应用工程师"},
        ).json()
        run_id = created["run_id"]

        for _ in range(100):
            if app.state.talent_run_manager.get_run(run_id).status == "waiting_input":
                break
            time.sleep(0.001)

        response = client.post(
            f"/api/runs/{run_id}/resume",
            json={"value": "UNKNOWN"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "请选择有效的岗位"


def test_history_endpoints_return_saved_run_and_detail():
    app = create_app(talent_run_manager=_history_manager())

    with TestClient(app) as client:
        history = client.get("/api/runs/history")
        detail = client.get("/api/runs/history/saved-run")

    assert history.status_code == 200
    assert history.json()[0]["run_id"] == "saved-run"
    assert detail.status_code == 200
    assert detail.json()["message"] == "筛选企业知识库项目候选人"


def test_history_detail_returns_404_for_unknown_run():
    app = create_app(talent_run_manager=_history_manager())

    with TestClient(app) as client:
        response = client.get("/api/runs/history/missing")

    assert response.status_code == 404


def test_conversation_list_supports_status_filter():
    app = create_app(talent_run_manager=_history_manager())

    with TestClient(app) as client:
        completed = client.get("/api/conversations?status=completed")
        running = client.get("/api/conversations?status=running")

    assert completed.status_code == 200
    assert completed.json()[0] == {
        "conversation_id": "saved-run",
        "title": "新会话",
        "status": "completed",
        "updated_at": "2026-10-11T01:00:05",
        "latest_run_id": "saved-run",
    }
    assert running.json() == []
