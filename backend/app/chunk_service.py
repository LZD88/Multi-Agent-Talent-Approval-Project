from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chunking import ChunkElement, ChunkStrategy, chunk_elements
from app.models import (
    ChunkingRun,
    ChunkingStatus,
    ChunkStrategyName,
    Document,
    DocumentChunk,
    DocumentVersion,
    ParseArtifact,
    ParseJob,
    ParseStatus,
)
from app.object_store import ObjectStore


def infer_chunk_strategy(elements: list[ChunkElement]) -> ChunkStrategy:
    return ChunkStrategy.MARKDOWN if any(element.kind == "heading" for element in elements) else ChunkStrategy.RECURSIVE


def parent_paths_for_heading(heading_path: list[str]) -> list[tuple[str, ...]]:
    return [tuple(heading_path[:index]) for index in range(1, len(heading_path) + 1)]


def content_element_ids(elements: list[ChunkElement]) -> list[str]:
    return [element.id for element in elements if element.kind != "heading"]


def _source_locator(item: dict, *, page: int | None = None) -> dict:
    locator = dict(item.get("locator") or {})
    if locator.get("kind") in {"page_region", "slide", "slide_region", "time_range"}:
        return locator
    locator = {}
    bbox = item.get("bbox")
    if page is not None:
        locator = {"kind": "page_region", "page": page}
        if bbox is not None:
            locator["bbox"] = bbox
    return locator


def _markdown_blocks(markdown: str) -> list[tuple[str, int, int]]:
    blocks: list[tuple[str, int, int]] = []
    for match in re.finditer(r"(?:^|\n\s*\n)(.*?)(?=\n\s*\n|\Z)", markdown, flags=re.DOTALL):
        raw = match.group(1)
        block = raw.strip()
        if not block:
            continue
        start = match.start(1) + len(raw) - len(raw.lstrip())
        heading_with_body = re.match(r"^(#{1,6}\s+[^\r\n]+)\r?\n([\s\S]+)$", block)
        if not heading_with_body:
            blocks.append((block, start, start + len(block)))
            continue

        heading = heading_with_body.group(1).rstrip()
        body_raw = heading_with_body.group(2)
        body = body_raw.strip()
        blocks.append((heading, start, start + len(heading)))
        if body:
            body_start = start + heading_with_body.start(2) + len(body_raw) - len(body_raw.lstrip())
            blocks.append((body, body_start, body_start + len(body)))
    return blocks


def elements_from_artifacts(markdown: str, structured: dict) -> list[ChunkElement]:
    content_list = structured.get("content_list") if isinstance(structured, dict) else None
    if not content_list and isinstance(structured, dict) and isinstance(structured.get("segments"), list):
        content_list = [
            {
                "type": "text",
                "text": item.get("text", ""),
                "locator": {
                    "kind": "time_range",
                    "segment": index,
                    "speaker": item.get("speaker"),
                    "timestamp_start": item.get("start"),
                    "timestamp_end": item.get("end"),
                },
            }
            for index, item in enumerate(structured["segments"], start=1)
        ]
    source_items: list[tuple[int, dict, str]] = []
    if isinstance(content_list, list):
        for index, item in enumerate(content_list, start=1):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("content") or "").strip()
            if text:
                source_items.append((index, item, text))

    if markdown.strip():
        blocks = _markdown_blocks(markdown)
        used_sources: set[int] = set()
        result: list[ChunkElement] = []
        for block_index, (block, markdown_start, markdown_end) in enumerate(blocks, start=1):
            normalized_block = re.sub(r"^#{1,6}\s+", "", block).strip()
            source = next(
                (
                    (index, item)
                    for index, item, text in source_items
                    if index not in used_sources and re.sub(r"\s+", " ", text) == re.sub(r"\s+", " ", normalized_block)
                ),
                None,
            )
            if source:
                source_index, source_item = source
                used_sources.add(source_index)
                page = int(source_item["page_idx"]) + 1 if source_item.get("page_idx") is not None else None
                element_id = f"p{page}-e{source_index}" if page is not None else f"md-e{block_index}"
            else:
                source_item = {}
                page = None
                element_id = f"md-e{block_index}"
            is_heading = bool(re.match(r"^#{1,6}\s+", block)) or source_item.get("type") in {"title", "heading"}
            locator = _source_locator(source_item, page=page)
            speaker = locator.get("speaker") or source_item.get("speaker")
            result.append(
                ChunkElement(
                    id=element_id,
                    text=block,
                    page=page,
                    kind="heading" if is_heading else str(source_item.get("type") or "text"),
                    timestamp_start=locator.get("timestamp_start"),
                    timestamp_end=locator.get("timestamp_end"),
                    speaker=str(speaker) if speaker else None,
                    markdown_start=markdown_start,
                    markdown_end=markdown_end,
                    source_locator=locator,
                )
            )
        return result

    result = []
    markdown_cursor = 0
    for index, item, text in source_items:
        page = int(item["page_idx"]) + 1 if item.get("page_idx") is not None else None
        kind = "heading" if item.get("type") in {"title", "heading"} else str(item.get("type") or "text")
        locator = _source_locator(item, page=page)
        speaker = locator.get("speaker") or item.get("speaker")
        result.append(
            ChunkElement(
                id=f"p{page}-e{index}" if page is not None else f"src-e{index}",
                text=text,
                page=page,
                kind=kind,
                timestamp_start=locator.get("timestamp_start"),
                timestamp_end=locator.get("timestamp_end"),
                speaker=str(speaker) if speaker else None,
                markdown_start=markdown_cursor,
                markdown_end=markdown_cursor + len(text),
                source_locator=locator,
            )
        )
        markdown_cursor += len(text) + 2
    return result


def _load_parse_input(db: Session, store: ObjectStore, version_id: UUID) -> tuple[ParseJob, str, dict]:
    job = db.scalar(
        select(ParseJob)
        .where(ParseJob.document_version_id == version_id, ParseJob.status == ParseStatus.SUCCEEDED)
        .order_by(ParseJob.created_at.desc())
    )
    if not job:
        raise ValueError("文档尚无成功的解析任务")
    artifacts = db.scalars(select(ParseArtifact).where(ParseArtifact.parse_job_id == job.id)).all()
    markdown = ""
    structured: dict = {}
    for artifact in artifacts:
        body = store.get_bytes(artifact.object_key)
        if artifact.artifact_type == "markdown":
            markdown = body.decode("utf-8", errors="replace")
        elif artifact.artifact_type == "structured_json":
            structured = json.loads(body.decode("utf-8"))
    if not markdown and not structured:
        raise ValueError("解析任务没有可用的 Markdown 或结构化产物")
    return job, markdown, structured


def create_chunking_run(
    db: Session,
    store: ObjectStore,
    document: Document,
    version: DocumentVersion,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> ChunkingRun:
    parse_job, markdown, structured = _load_parse_input(db, store, version.id)
    elements = elements_from_artifacts(markdown, structured)
    strategy = infer_chunk_strategy(elements)
    run = ChunkingRun(
        parse_job_id=parse_job.id,
        strategy=ChunkStrategyName(strategy.value),
        status=ChunkingStatus.RUNNING,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    db.add(run)
    db.flush()
    try:
        pieces = chunk_elements(elements, strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        children: list[DocumentChunk] = []
        parent_by_path: dict[tuple[str, ...], DocumentChunk] = {}
        for position, piece in enumerate(pieces):
            parent = None
            parents_for_piece: list[DocumentChunk] = []
            for path_key in parent_paths_for_heading(piece.heading_path):
                current_parent = parent_by_path.get(path_key)
                if current_parent is None:
                    current_parent = DocumentChunk(
                        chunking_run_id=run.id,
                        document_version_id=version.id,
                        candidate_id=document.candidate_id,
                        document_type=document.document_type,
                        permission_scope=document.permission_scope,
                        stable_key=_stable_key(version.id, strategy, f"parent:{'/'.join(path_key)}"),
                        position=position,
                        chunk_level="parent",
                        content="",
                        element_ids=[],
                        heading_path=list(path_key),
                        parent_chunk_id=parent.id if parent else None,
                        markdown_start=None,
                        markdown_end=None,
                        source_locators=[],
                    )
                    db.add(current_parent)
                    db.flush()
                    parent_by_path[path_key] = current_parent
                parent = current_parent
                parents_for_piece.append(current_parent)
            child = DocumentChunk(
                chunking_run_id=run.id,
                document_version_id=version.id,
                candidate_id=document.candidate_id,
                document_type=document.document_type,
                permission_scope=document.permission_scope,
                stable_key=_stable_key(version.id, strategy, f"{position}:{piece.content}"),
                position=position,
                chunk_level="child",
                content=piece.content,
                element_ids=piece.element_ids,
                heading_path=piece.heading_path,
                parent_chunk_id=parent.id if parent else None,
                page_start=piece.page_start,
                page_end=piece.page_end,
                timestamp_start=piece.timestamp_start,
                timestamp_end=piece.timestamp_end,
                markdown_start=piece.markdown_start,
                markdown_end=piece.markdown_end,
                source_locators=piece.source_locators,
            )
            db.add(child)
            db.flush()
            for current_parent in parents_for_piece:
                current_parent.content = f"{current_parent.content}\n\n{child.content}".strip()
                current_parent.element_ids = list(dict.fromkeys([*current_parent.element_ids, *child.element_ids]))
                if child.page_start is not None:
                    current_parent.page_start = min(current_parent.page_start, child.page_start) if current_parent.page_start is not None else child.page_start
                if child.page_end is not None:
                    current_parent.page_end = max(current_parent.page_end, child.page_end) if current_parent.page_end is not None else child.page_end
                if child.markdown_start is not None:
                    current_parent.markdown_start = min(current_parent.markdown_start, child.markdown_start) if current_parent.markdown_start is not None else child.markdown_start
                if child.markdown_end is not None:
                    current_parent.markdown_end = max(current_parent.markdown_end, child.markdown_end) if current_parent.markdown_end is not None else child.markdown_end
                existing_locators = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in current_parent.source_locators}
                current_parent.source_locators = [
                    *current_parent.source_locators,
                    *(item for item in child.source_locators if json.dumps(item, ensure_ascii=False, sort_keys=True) not in existing_locators),
                ]
            if children:
                child.previous_chunk_id = children[-1].id
                children[-1].next_chunk_id = child.id
            children.append(child)
        run.status = ChunkingStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        run.status = ChunkingStatus.FAILED
        run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(UTC)
        db.commit()
        raise


def _stable_key(version_id: UUID, strategy: ChunkStrategy, value: str) -> str:
    return hashlib.sha256(f"{version_id}:{strategy.value}:{value}".encode()).hexdigest()[:32]


def latest_chunks(db: Session, version_id: UUID) -> tuple[ChunkingRun | None, list[DocumentChunk]]:
    run = db.scalar(
        select(ChunkingRun)
        .join(ParseJob, ParseJob.id == ChunkingRun.parse_job_id)
        .where(ParseJob.document_version_id == version_id, ChunkingRun.status == ChunkingStatus.SUCCEEDED)
        .order_by(ChunkingRun.created_at.desc())
    )
    if not run:
        return None, []
    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.chunking_run_id == run.id).order_by(DocumentChunk.chunk_level, DocumentChunk.position)
    ).all()
    return run, chunks
