"""通过真实 FastAPI Run 、模型与 PostgreSQL 验证 Case 自动采集。"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx
from sqlalchemy import select

from app.database import SessionLocal
from app.models import EvaluationCaseRecord


DEFAULT_QUESTION = (
    "筛选上海地区、至少 5 年开发经验，"
    "并且做过企业知识库 RAG 项目的高级 AI 应用工程师"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="验证真实 Run 是否自动写入人才要求理解 Case"
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:18080")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    return parser


def _run_through_api(api_base: str, question: str) -> tuple[dict[str, Any], list[str]]:
    with httpx.Client(timeout=300) as client:
        created = client.post(
            f"{api_base}/api/runs",
            json={"message": question},
        )
        created.raise_for_status()
        run = created.json()
        event_types: list[str] = []
        terminal: dict[str, Any] | None = None
        with client.stream("GET", f"{api_base}{run['events_url']}") as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("event: "):
                    event_types.append(line.removeprefix("event: "))
                elif line.startswith("data: "):
                    payload = json.loads(line.removeprefix("data: "))
                    if payload["type"] in {"run.completed", "run.failed", "run.cancelled"}:
                        terminal = payload
        if terminal is None:
            raise AssertionError("SSE 未返回 Run 终态")
        if terminal["type"] != "run.completed":
            raise AssertionError(f"Run 未完成: {terminal}")
        return run, event_types


def _load_case(run_id: str) -> EvaluationCaseRecord:
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(EvaluationCaseRecord)
                .where(EvaluationCaseRecord.source_run_id == run_id,
                       EvaluationCaseRecord.module_key == "request_understanding")
                .order_by(EvaluationCaseRecord.invocation_no)
            )
        )
    if not rows:
        raise AssertionError(f"Run {run_id} 没有自动采集 Case")
    return rows[0]


def main() -> None:
    args = _parser().parse_args()
    run, event_types = _run_through_api(args.api_base.rstrip("/"), args.question)
    case = _load_case(run["run_id"])
    filters = case.output_payload["draft"]["filters"]
    print(f"run_id={run['run_id']}")
    print(f"conversation_id={run['conversation_id']}")
    print(f"turn_no={run['turn_no']}")
    print(f"run_status={event_types[-1]}")
    print(f"module_key={case.module_key}")
    print(f"invocation_no={case.invocation_no}")
    print(f"case_status={case.status}")
    print(f"duration_ms={case.duration_ms}")
    print(f"case_message_count={len(case.input_payload['messages'])}")
    print(f"input_mode={case.output_payload['draft']['input_mode']}")
    print("filters=" + json.dumps(filters, ensure_ascii=False, separators=(",", ":")))
    print(f"annotation_status={case.annotation_status}")


if __name__ == "__main__":
    main()
