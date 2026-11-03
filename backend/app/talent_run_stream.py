from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from langchain.messages import HumanMessage
from langgraph.types import Command

from app.config import get_settings
from app.talent_checkpoint import PostgresCheckpointRuntime
from app.talent_decision_graph import DecisionContext
from app.talent_event_projection import (
    interrupt_payload,
    project_graph_update,
    step_for_start,
)


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.CANCELLED,
    RunStatus.FAILED,
}

FAILED_GRAPH_STATUSES = {"capacity_exceeded", "dimension_invalid", "failed"}


@dataclass(frozen=True)
class RunEvent:
    event_id: str
    sequence: int
    type: str
    run_id: str
    timestamp: str
    data: dict[str, Any]
    schema_version: str = "1.0"


@dataclass
class TalentRun:
    run_id: str
    conversation_id: str
    turn_no: int
    message: str
    status: RunStatus = RunStatus.RUNNING
    events: list[RunEvent] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    pending_interrupt: dict[str, Any] | None = None
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    started_steps: set[str] = field(default_factory=set)
    completed_steps: set[str] = field(default_factory=set)
    branch_completed: int = 0
    branch_total: int = 0
    extraction_failures: int = 0
    branch_status_counts: dict[str, int] = field(default_factory=dict)
    graph_status: str = ""
    graph_errors: list[str] = field(default_factory=list)
    report_emitted: bool = False


class TalentRunManager:
    """Stream the real decision graph and retain events for the active SSE stream."""

    def __init__(
        self,
        *,
        graph: Any | None = None,
        context: DecisionContext | None = None,
        max_concurrency: int = 6,
        record_store: Any | None = None,
    ) -> None:
        self._graph = graph
        self._checkpoint_runtime: PostgresCheckpointRuntime | None = None
        self._context = context or DecisionContext(
            tenant_id="course-demo",
            permission_scopes=("hr_private",),
        )
        self._max_concurrency = max_concurrency
        self._record_store = record_store
        self._runs: dict[str, TalentRun] = {}
        self._conversation_runs: dict[str, list[str]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}

    async def startup(self) -> None:
        if self._graph is not None:
            return
        from app.talent_decision_runtime import build_runtime_decision_graph

        self._checkpoint_runtime = PostgresCheckpointRuntime(
            get_settings().database_url
        )
        saver = await self._checkpoint_runtime.start()
        self._graph = build_runtime_decision_graph(checkpointer=saver)

    async def shutdown(self) -> None:
        if self._checkpoint_runtime is not None:
            await self._checkpoint_runtime.close()
            self._checkpoint_runtime = None

    def _get_graph(self):
        """仅供已注入 Graph 的测试与组合代码读取。"""

        if self._graph is None:
            raise RuntimeError("LangGraph 运行时尚未启动")
        return self._graph

    async def create_run(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> TalentRun:
        await self.startup()
        conversation_key = conversation_id or str(uuid4())
        run_ids = self._conversation_runs.setdefault(conversation_key, [])
        if run_ids and self.get_run(run_ids[-1]).status not in TERMINAL_STATUSES:
            raise ValueError("当前会话仍有运行中的任务")
        run = TalentRun(
            run_id=str(uuid4()),
            conversation_id=conversation_key,
            turn_no=(
                self._record_store.next_turn_no(conversation_key)
                if self._record_store is not None
                else len(run_ids) + 1
            ),
            message=message.strip(),
        )
        self._runs[run.run_id] = run
        run_ids.append(run.run_id)
        self._conditions[run.run_id] = asyncio.Condition()
        self._append(
            run,
            "run.started",
            {
                "message": run.message,
                "conversation_id": run.conversation_id,
                "turn_no": run.turn_no,
            },
        )
        graph_input: dict[str, Any] = {
            "messages": [HumanMessage(content=run.message)],
            "request_text": run.message,
        }
        run.task = asyncio.create_task(self._execute(run, graph_input))
        return run

    def get_run(self, run_id: str) -> TalentRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise KeyError("运行不存在") from exc

    def events_since(self, run_id: str, cursor: int) -> list[RunEvent]:
        run = self.get_run(run_id)
        return [event for event in run.events if event.sequence > cursor]

    def is_stream_finished(self, run_id: str) -> bool:
        return self.get_run(run_id).status in TERMINAL_STATUSES

    async def resume_run(self, run_id: str, value: str) -> TalentRun:
        run = self.get_run(run_id)
        if run.status != RunStatus.WAITING_INPUT or run.pending_interrupt is None:
            raise ValueError("当前运行不在等待人工输入")
        decision, resolved = self._resume_value(run.pending_interrupt, value.strip())
        run.pending_interrupt = None
        run.status = RunStatus.RUNNING
        self._append(run, "hitl.resolved", resolved)
        run.task = asyncio.create_task(self._execute(run, Command(resume=decision)))
        await self._notify(run.run_id)
        return run

    async def cancel_run(self, run_id: str) -> TalentRun:
        run = self.get_run(run_id)
        if run.status in TERMINAL_STATUSES:
            return run
        run.status = RunStatus.CANCELLED
        if run.task and not run.task.done():
            run.task.cancel()
        self._append(run, "run.cancelled", {"reason": "user_requested"})
        await self._notify(run.run_id)
        return run

    async def wait_for_change(
        self,
        run_id: str,
        cursor: int,
        *,
        timeout: float = 15,
    ) -> None:
        run = self.get_run(run_id)
        if len(run.events) > cursor or self.is_stream_finished(run_id):
            return
        condition = self._conditions[run_id]
        async with condition:
            try: # SSE 协程持续等待通知，直到收到新事件或超时
                await asyncio.wait_for(condition.wait(), timeout=timeout)
            except TimeoutError:
                return

    def _append(self, run: TalentRun, event_type: str, data: dict[str, Any]) -> RunEvent:
        sequence = len(run.events) + 1
        event = RunEvent(
            event_id=f"{run.run_id}:{sequence}",
            sequence=sequence,
            type=event_type,
            run_id=run.run_id,
            timestamp=datetime.now(UTC).isoformat(),
            data=data,
        )
        run.events.append(event)
        if self._record_store is not None:
            self._record_store.record(event)
        return event

    def list_history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if self._record_store is None:
            return []
        return self._record_store.list_runs(limit=limit)

    def get_history(self, run_id: str) -> dict[str, Any] | None:
        if self._record_store is None:
            return None
        return self._record_store.get_run(run_id)

    def list_conversations(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if self._record_store is None:
            return []
        return self._record_store.list_conversations(status=status, limit=limit)

    async def _emit(self, run: TalentRun, event_type: str, data: dict[str, Any]) -> None:
        if run.status == RunStatus.CANCELLED and event_type != "run.cancelled":
            return
        self._append(run, event_type, data)
        await self._notify(run.run_id)

    async def _notify(self, run_id: str) -> None:
        condition = self._conditions[run_id]
        async with condition:
            condition.notify_all() # 通知所有等待 SSE 的协程，表示有新事件可用

    def _config(self, run: TalentRun) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": run.conversation_id},
            "max_concurrency": self._max_concurrency,
        }

    async def _execute(self, run: TalentRun, graph_input: Any) -> None:
        try:
            graph = self._graph
            if graph is None:
                raise RuntimeError("LangGraph 运行时未启动")
            run_context = DecisionContext(
                tenant_id=self._context.tenant_id,
                permission_scopes=self._context.permission_scopes,
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                turn_no=run.turn_no,
            )
            async for item in graph.astream(
                graph_input,
                self._config(run),
                context=run_context,
                stream_mode=["tasks", "updates"],
                subgraphs=True,
            ):
                if run.status == RunStatus.CANCELLED:
                    return
                namespace, mode, chunk = item
                if mode == "tasks":
                    await self._handle_task_event(run, chunk)
                elif mode == "updates":
                    interrupted = await self._handle_updates(run, namespace, chunk)
                    if interrupted:
                        return

            if run.status == RunStatus.CANCELLED:
                return
            if run.graph_status == "cancelled":
                run.status = RunStatus.CANCELLED
                await self._emit(run, "run.cancelled", {"reason": "graph_cancelled"})
                return
            if run.graph_status in FAILED_GRAPH_STATUSES:
                run.status = RunStatus.FAILED
                message = "；".join(run.graph_errors) or f"运行失败：{run.graph_status}"
                await self._emit(
                    run,
                    "run.failed",
                    {"message": message, "graph_status": run.graph_status},
                )
                return
            run.status = RunStatus.COMPLETED
            await self._emit(
                run,
                "run.completed",
                {
                    "message": "评估完成",
                    "graph_status": run.graph_status or "completed",
                    "run_id": run.run_id,
                },
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if run.status == RunStatus.CANCELLED:
                return
            run.status = RunStatus.FAILED
            await self._emit(run, "run.failed", {"message": str(exc)})

    async def _handle_task_event(self, run: TalentRun, chunk: Any) -> None:
        if not isinstance(chunk, dict) or "result" in chunk or "error" in chunk:
            return
        node = str(chunk.get("name", ""))
        step = step_for_start(node)
        if step is None:
            return
        step_id, kind, title = step
        if step_id in run.started_steps:
            return
        run.started_steps.add(step_id)
        data: dict[str, Any] = {
            "step_id": step_id,
            "kind": kind,
            "title": title,
        }
        if step_id == "parallel_assessment" and run.branch_total:
            data["total"] = run.branch_total
        await self._emit(run, "step.started", data)

    async def _handle_updates(
        self,
        run: TalentRun,
        namespace: tuple[str, ...],
        chunk: Any,
    ) -> bool:
        if not isinstance(chunk, dict):
            return False
        for node, update in chunk.items():
            if node == "__interrupt__":
                payload = interrupt_payload(update)
                if payload is None:
                    raise RuntimeError("未能解析 LangGraph interrupt 载荷")
                run.pending_interrupt = payload
                run.status = RunStatus.WAITING_INPUT
                projected = project_graph_update(
                    namespace=namespace,
                    node=node,
                    update=update,
                )
                if projected:
                    await self._emit(run, projected[0]["type"], projected[0]["data"])
                return True
            if not isinstance(update, dict):
                continue
            if node == "interpret_input" and update.get("conversation_title"):
                await self._emit(
                    run,
                    "conversation.updated",
                    {"title": str(update["conversation_title"])},
                )
                action = str(update.get("revision_action", ""))
                if action in {"add", "update", "remove"}:
                    await self._emit(
                        run,
                        "requirement.revised",
                        {
                            "action": action,
                            "summary": str(update.get("revision_summary", "")),
                        },
                    )
            status = update.get("status")
            if isinstance(status, str):
                run.graph_status = status
            errors = update.get("errors")
            if isinstance(errors, list):
                for error in errors:
                    if isinstance(error, str) and error and error not in run.graph_errors:
                        run.graph_errors.append(error)
            self._collect_evidence(run, update)
            await self._project_progress(run, node, update)
            for event in project_graph_update(
                namespace=namespace,
                node=node,
                update=update,
            ):
                await self._emit_projected(run, event)
            if node == "validate_report" and update.get("report"):
                await self._emit_report(run, str(update["report"]))
        return False

    def _collect_evidence(self, run: TalentRun, update: dict[str, Any]) -> None:
        raw_items = update.get("evidence_items")
        if not isinstance(raw_items, list):
            return
        seen = {
            str(item.get("ref_id") or item.get("model_ref"))
            for item in run.evidence_items
        }
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            identity = str(item.get("ref_id") or item.get("model_ref"))
            if identity and identity not in seen:
                run.evidence_items.append(item)
                seen.add(identity)

    async def _project_progress(
        self,
        run: TalentRun,
        node: str,
        update: dict[str, Any],
    ) -> None:
        if node == "prepare_work_items":
            total = update.get("required_work_items")
            if isinstance(total, int):
                run.branch_total = total
                await self._emit(
                    run,
                    "step.progress",
                    {
                        "step_id": "parallel_assessment",
                        "kind": "parallel",
                        "title": "检索证据并并行评估",
                        "completed": run.branch_completed,
                        "total": total,
                    },
                )
        elif node == "run_assessment_branch":
            results = update.get("branch_results")
            normalized_results = results if isinstance(results, list) else []
            run.branch_completed += len(normalized_results) or 1
            for result in normalized_results:
                if not isinstance(result, dict):
                    continue
                status = str(result.get("execution_status", "unknown"))
                run.branch_status_counts[status] = run.branch_status_counts.get(status, 0) + 1
                run.extraction_failures += sum(
                    1
                    for requirement in result.get("requirements", [])
                    if isinstance(requirement, dict)
                    and requirement.get("extraction_status") == "failed"
                )
            await self._emit(
                run,
                "step.progress",
                {
                    "step_id": "parallel_assessment",
                    "kind": "parallel",
                    "title": "检索证据并并行评估",
                    "completed": run.branch_completed,
                    "total": run.branch_total,
                },
            )

    async def _emit_projected(self, run: TalentRun, event: dict[str, Any]) -> None:
        event_type = str(event["type"])
        data = dict(event["data"])
        step_id = str(data.get("step_id", ""))
        if event_type == "step.completed":
            if step_id in run.completed_steps:
                return
            if step_id not in run.started_steps:
                run.started_steps.add(step_id)
                await self._emit(
                    run,
                    "step.started",
                    {key: data[key] for key in ("step_id", "kind", "title")},
                )
            run.completed_steps.add(step_id)
            if step_id == "parallel_assessment":
                statuses = "、".join(
                    f"{name}={count}"
                    for name, count in sorted(run.branch_status_counts.items())
                )
                data["summary"] = (
                    f"{run.branch_completed} 个评估分支完成"
                    f"，取证失败={run.extraction_failures}"
                    + (f"，{statuses}" if statuses else "")
                )
                data["output"] = {
                    "status": run.graph_status or "branches_ready",
                    "completed": run.branch_completed,
                    "total": run.branch_total,
                    "extraction_failures": run.extraction_failures,
                    "branch_status_counts": dict(run.branch_status_counts),
                }
        await self._emit(run, event_type, data)

    async def _emit_report(self, run: TalentRun, markdown: str) -> None:
        if run.report_emitted:
            return
        run.report_emitted = True
        citations = []
        for item in run.evidence_items:
            ref = item.get("model_ref")
            if not ref:
                continue
            citations.append(
                {
                    "ref": str(ref),
                    "candidate": str(item.get("candidate_id", "")),
                    "document": str(item.get("source_label", "")),
                    "quote": str(item.get("quote", "")),
                    "url": str(item.get("citation_url", "")),
                }
            )
        await self._emit(
            run,
            "report.completed",
            {
                "markdown": markdown,
                "citations": citations,
                "trace_id": run.run_id,
            },
        )

    @staticmethod
    def _resume_value(
        payload: dict[str, Any],
        value: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        interaction_type = payload.get("type")
        if interaction_type == "job_selection":
            options = {
                str(item.get("job_code")): str(item.get("name"))
                for item in payload.get("options", [])
                if isinstance(item, dict) and item.get("job_code")
            }
            if value not in options:
                raise ValueError("请选择有效的岗位")
            return (
                {"action": "select", "job_code": value},
                {"interaction_type": interaction_type, "value": value, "label": options[value]},
            )
        if interaction_type == "requirement_revision":
            if not value:
                raise ValueError("修订后的人才要求不能为空")
            interaction_id = str(payload.get("interaction_id", ""))
            return (
                {
                    "interaction_id": interaction_id,
                    "action": "revise",
                    "revision_text": value,
                },
                {"interaction_type": interaction_type, "interaction_id": interaction_id},
            )
        raise ValueError(f"暂不支持的人工交互类型: {interaction_type}")

