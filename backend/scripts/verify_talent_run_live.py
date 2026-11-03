"""第 19 课：使用真实数据、检索服务和模型验证 Run 与 SSE 投影链路。"""

from __future__ import annotations

import argparse
import asyncio
import json
from time import perf_counter

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Document, DocumentChunk, EmployeeProfile, JobDescription
from app.talent_run_stream import RunStatus, TalentRunManager


DEFAULT_QUESTION = (
    "请按照岗位编号 JD-AI-001 为上海团队筛选高级 AI 应用工程师，要求至少 5 年开发经验，"
    "并且做过企业知识库 RAG 项目。"
    "请基于现有简历筛选最匹配的候选人，给出证据、风险和录用建议。"
)
DEFAULT_HITL_QUESTION = (
    "我们准备启动 AI 应用工程师岗位的招聘，请先从系统中的岗位 JD 里找到对应岗位，"
    "再基于岗位要求评估现有候选人，并给出证据、风险和录用建议"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证真实人才评估 Run 链路")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--hitl-question", default=DEFAULT_HITL_QUESTION)
    parser.add_argument("--job-code", default="JD-AI-001")
    parser.add_argument("--max-concurrency", type=int, default=6)
    return parser


def _data_inventory() -> dict[str, int]:
    with SessionLocal() as db:
        return {
            "employee_profiles": db.scalar(select(func.count()).select_from(EmployeeProfile)) or 0,
            "job_descriptions": db.scalar(select(func.count()).select_from(JobDescription)) or 0,
            "documents": db.scalar(select(func.count()).select_from(Document)) or 0,
            "document_chunks": db.scalar(select(func.count()).select_from(DocumentChunk)) or 0,
        }


async def _verify_hitl(
    manager: TalentRunManager,
    question: str,
    job_code: str,
) -> dict[str, object]:
    run = await manager.create_run(question)
    cursor = 0
    while manager.get_run(run.run_id).status == RunStatus.RUNNING:
        await manager.wait_for_change(run.run_id, cursor, timeout=10)
        events = manager.events_since(run.run_id, cursor)
        if events:
            cursor = events[-1].sequence
    current = manager.get_run(run.run_id)
    if current.status != RunStatus.WAITING_INPUT:
        failure = next(
            (
                event.data.get("message")
                for event in reversed(current.events)
                if event.type == "run.failed"
            ),
            None,
        )
        raise AssertionError(
            f"HITL Run 未进入等待状态: {current.status}; {failure}"
        )
    required = next(
        (event for event in current.events if event.type == "hitl.required"),
        None,
    )
    if required is None:
        raise AssertionError("HITL Run 缺少 hitl.required")
    await manager.resume_run(run.run_id, job_code)
    resolved = next(
        (event for event in current.events if event.type == "hitl.resolved"),
        None,
    )
    if resolved is None or resolved.sequence != required.sequence + 1:
        raise AssertionError("HITL 恢复事件未紧接中断事件")
    await manager.cancel_run(run.run_id)
    return {
        "run_id": run.run_id,
        "required_sequence": required.sequence,
        "resolved_sequence": resolved.sequence,
        "selected_job_code": job_code,
    }


async def verify(
    question: str,
    hitl_question: str,
    job_code: str,
    max_concurrency: int,
) -> dict[str, object]:
    manager = TalentRunManager(max_concurrency=max_concurrency)
    hitl_result = await _verify_hitl(manager, hitl_question, job_code)
    started = perf_counter()
    run = await manager.create_run(question)
    cursor = 0
    while manager.get_run(run.run_id).status == RunStatus.RUNNING:
        await manager.wait_for_change(run.run_id, cursor, timeout=10)
        events = manager.events_since(run.run_id, cursor)
        if events:
            cursor = events[-1].sequence

    if manager.get_run(run.run_id).status == RunStatus.WAITING_INPUT:
        await manager.resume_run(run.run_id, job_code)
        while manager.get_run(run.run_id).status == RunStatus.RUNNING:
            await manager.wait_for_change(run.run_id, cursor, timeout=10)
            events = manager.events_since(run.run_id, cursor)
            if events:
                cursor = events[-1].sequence

    current = manager.get_run(run.run_id)
    events = manager.events_since(run.run_id, 0)
    report_event = next(
        (event for event in events if event.type == "report.completed"),
        None,
    )
    event_types = [event.type for event in events]
    if current.status != RunStatus.COMPLETED:
        failure = next(
            (event.data.get("message") for event in reversed(events) if event.type == "run.failed"),
            None,
        )
        raise AssertionError(f"Run 未完成: {current.status}; {failure}")
    if current.branch_total < 1 or current.branch_completed != current.branch_total:
        raise AssertionError(
            "评估分支未全部完成: "
            f"total={current.branch_total}, completed={current.branch_completed}, "
            f"graph_status={current.graph_status}, events={event_types}"
        )
    if current.extraction_failures:
        raise AssertionError(f"存在 {current.extraction_failures} 个取证失败")
    if report_event is None or not report_event.data.get("citations"):
        raise AssertionError("报告或可回溯引用缺失")
    if [event.sequence for event in events] != list(range(1, len(events) + 1)):
        raise AssertionError("事件顺序号不连续")
    required = {
        "run.started",
        "step.started",
        "step.progress",
        "step.completed",
        "report.completed",
        "run.completed",
    }
    if missing := required.difference(event_types):
        raise AssertionError(f"事件链路不完整: {sorted(missing)}")
    return {
        "hitl": hitl_result,
        "evaluation_run_id": run.run_id,
        "status": current.status,
        "elapsed_seconds": round(perf_counter() - started, 2),
        "data_inventory": _data_inventory(),
        "event_count": len(events),
        "branch_total": current.branch_total,
        "branch_status_counts": current.branch_status_counts,
        "extraction_failures": current.extraction_failures,
        "citation_count": len(report_event.data["citations"]),
        "report_chars": len(str(report_event.data["markdown"])),
    }


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(
        verify(
            args.question,
            args.hitl_question,
            args.job_code,
            args.max_concurrency,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
