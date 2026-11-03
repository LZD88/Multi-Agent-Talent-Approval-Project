from __future__ import annotations

import json
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.talent_run_stream import RunEvent, TalentRunManager


router = APIRouter(prefix="/api/runs", tags=["talent-runs"])
conversation_router = APIRouter(prefix="/api/conversations", tags=["talent-conversations"])


class CreateRunInput(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class ResumeRunInput(BaseModel):
    value: str = Field(min_length=1, max_length=200)


def encode_sse(event: RunEvent) -> str:
    payload = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {payload}\n\n"


def _manager(request: Request) -> TalentRunManager:
    return request.app.state.talent_run_manager


@router.get("/history")
async def list_run_history(request: Request, limit: int = 20):
    return _manager(request).list_history(limit=max(1, min(limit, 100)))


@router.get("/history/{run_id}")
async def get_run_history(run_id: str, request: Request):
    detail = _manager(request).get_history(run_id)
    if detail is None:
        raise HTTPException(404, "运行记录不存在")
    return detail


@conversation_router.get("")
async def list_conversations(
    request: Request,
    status: str | None = None,
    limit: int = 20,
):
    return _manager(request).list_conversations(
        status=status,
        limit=max(1, min(limit, 100)),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_run(payload: CreateRunInput, request: Request):
    try:
        run = await _manager(request).create_run(
            payload.message,
            conversation_id=payload.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "turn_no": run.turn_no,
        "status": run.status,
        "events_url": f"/api/runs/{run.run_id}/events",
    }


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
):
    manager = _manager(request)
    try:
        manager.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    cursor = 0

    async def generate() -> AsyncIterator[str]:
        nonlocal cursor
        yield "retry: 1500\n\n"
        while True:
            events = manager.events_since(run_id, cursor)
            for event in events:
                cursor = event.sequence
                yield encode_sse(event)
            if manager.is_stream_finished(run_id):
                return
            await manager.wait_for_change(run_id, cursor)
            if await request.is_disconnected():
                return
            if not manager.events_since(run_id, cursor):
                yield ": heartbeat\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, payload: ResumeRunInput, request: Request):
    manager = _manager(request)
    try:
        run = await manager.resume_run(run_id, payload.value)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run.run_id, "status": run.status}


@router.delete("/{run_id}")
async def cancel_run(run_id: str, request: Request):
    try:
        run = await _manager(request).cancel_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"run_id": run.run_id, "status": run.status}
