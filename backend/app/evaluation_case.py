from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from langchain.messages import AnyMessage
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from hashlib import sha256
from sqlalchemy.orm import sessionmaker

from app.models import EvaluationCaseRecord


class EvaluationModule(StrEnum):
    REQUEST_UNDERSTANDING = "request_understanding"
    JOB_MATCHING = "job_matching"
    DIMENSION_GENERATION = "dimension_generation"
    FACT_EXTRACTION = "fact_extraction"


class EvaluationCaseDraft(BaseModel):
    """A replayable snapshot captured at a real module invocation boundary."""

    schema_version: Literal["1.0"] = "1.0"
    source_run_id: str
    conversation_id: str
    turn_no: int
    module_key: EvaluationModule
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    replay_context: dict[str, Any] = Field(default_factory=dict)
    version_snapshot: dict[str, str] = Field(default_factory=dict)
    status: Literal["succeeded", "failed"] = "succeeded"
    error_code: str | None = None
    duration_ms: int | None = None


class SqlEvaluationCaseStore:
    """Persist raw module calls without adding labels or golden answers."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def record(self, draft: EvaluationCaseDraft) -> EvaluationCaseRecord:
        with self._session_factory() as session:
            if session.bind.dialect.name == "postgresql":
                lock_key = int.from_bytes(sha256(
                    f"{draft.source_run_id}:{draft.module_key.value}".encode()
                ).digest()[:8], "big", signed=True) # 事务锁，防止并发写入时出现唯一键冲突
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            last_no = session.scalar(
                select(func.max(EvaluationCaseRecord.invocation_no)).where(
                    EvaluationCaseRecord.source_run_id == draft.source_run_id,
                    EvaluationCaseRecord.module_key == draft.module_key.value,
                )
            )
            record = EvaluationCaseRecord(
                source_run_id=draft.source_run_id,
                conversation_id=draft.conversation_id,
                turn_no=draft.turn_no,
                module_key=draft.module_key.value,
                invocation_no=(last_no or 0) + 1,
                input_payload=draft.input_payload,
                output_payload=draft.output_payload,
                replay_context=draft.replay_context,
                version_snapshot=draft.version_snapshot,
                status=draft.status,
                error_code=draft.error_code,
                duration_ms=draft.duration_ms,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record


def recent_request_messages(
    messages: list[AnyMessage],
    *,
    previous_turn_limit: int = 3,
) -> list[AnyMessage]:
    """Return the current turn and at most the configured previous turns."""

    if previous_turn_limit < 0:
        raise ValueError("previous_turn_limit 不能小于 0")
    return list(messages[-(previous_turn_limit + 1) :])


def build_request_understanding_case(
    *,
    source_run_id: str,
    conversation_id: str,
    turn_no: int,
    messages: list[AnyMessage],
    previous_request: dict[str, Any],
    conversation_title: str,
    output_payload: dict[str, Any],
    replay_context: dict[str, Any],
    version_snapshot: dict[str, str],
    selected_job: dict[str, Any] | None = None,
    status: Literal["succeeded", "failed"] = "succeeded",
    error_code: str | None = None,
    duration_ms: int | None = None,
) -> EvaluationCaseDraft:
    recent_messages = recent_request_messages(messages)
    return EvaluationCaseDraft(
        source_run_id=source_run_id,
        conversation_id=conversation_id,
        turn_no=turn_no,
        module_key=EvaluationModule.REQUEST_UNDERSTANDING,
        input_payload={
            "messages": [
                {
                    "role": getattr(message, "type", "human"),
                    "content": str(message.content),
                }
                for message in recent_messages
            ],
            "previous_request": previous_request,
            "conversation_title": conversation_title,
            "selected_job": selected_job,
        },
        output_payload=output_payload,
        replay_context=replay_context,
        version_snapshot=version_snapshot,
        status=status,
        error_code=error_code,
        duration_ms=duration_ms,
    )


# Each extraction thread enters its own scope; no mutable global Run identity.
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from time import perf_counter
import logging

_capture_scope = ContextVar('evaluation_capture_scope', default=None)
logger = logging.getLogger(__name__)


@contextmanager
def extraction_scope(context, **identity):
    token = _capture_scope.set((context, identity))
    try:
        yield
    finally:
        _capture_scope.reset(token)


def current_extraction_scope():
    return _capture_scope.get()


def safe_record(recorder, draft):
    try:
        return recorder(draft)
    except Exception as exc:
        logger.error('event=case_collection_failed run_id=%s module=%s error_type=%s',
                     draft.source_run_id, draft.module_key, type(exc).__name__)
        return None


def capture_call(call, *, context, module_key, input_payload, recorder=None,
                 version_snapshot=None, replay_extra=None):
    if context is None or not context.run_id:
        return call()
    if recorder is None:
        from app.database import SessionLocal
        recorder = SqlEvaluationCaseStore(SessionLocal).record
    snapshot = deepcopy(input_payload) # 1. 深拷贝输入
    started = perf_counter()
    output, error = {}, None
    try:
        result = call() # 2. 执行原有逻辑
        output = result.model_dump(mode='json') if isinstance(result, BaseModel) else deepcopy(result)
        return result # 3. 返回结果
    except Exception as exc: # 4. 原样返回执行模块内的异常
        error = type(exc).__name__
        raise
    finally:
        safe_record(recorder, EvaluationCaseDraft(
            source_run_id=context.run_id, conversation_id=context.conversation_id,
            turn_no=context.turn_no, module_key=module_key, input_payload=snapshot,
            output_payload=output, replay_context={
                'tenant_id': context.tenant_id,
                'permission_scopes': list(context.permission_scopes), **(replay_extra or {}),
            }, version_snapshot=version_snapshot or {},
            status='failed' if error else 'succeeded', error_code=error,
            duration_ms=round((perf_counter() - started) * 1000),
        ))
