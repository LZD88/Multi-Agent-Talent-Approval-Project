from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.hybrid_search_service import RankedEvidence
from app.models import ChunkingRun, ChunkingStatus, Document, DocumentChunk, DocumentVersion


def search_terms(query: str) -> list[str]:
    """Extract stable lexical terms for an offline evidence-search fallback."""
    terms = re.findall(r"[A-Za-z][A-Za-z0-9+.#_-]{1,}|[\u4e00-\u9fff]{2,8}", query)
    ignored = {"相关经验", "项目经历", "具体说明", "候选人", "简历或项目", "明确出现"}
    return list(dict.fromkeys(term.lower() for term in terms if term not in ignored))


def rank_chunks(query: str, chunks: list[Any], *, limit: int = 8) -> list[RankedEvidence]:
    terms = search_terms(query)
    ranked: list[RankedEvidence] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        content = str(chunk.content)
        lowered = content.lower()
        matched = [term for term in terms if term in lowered]
        if not matched:
            continue
        key = (str(chunk.candidate_id), content)
        if key in seen:
            continue
        seen.add(key)
        occurrence_count = sum(lowered.count(term) for term in matched)
        score = round(len(matched) + min(occurrence_count, 20) / 100, 4)
        ranked.append(
            RankedEvidence(
                chunk_id=str(chunk.id),
                candidate_id=str(chunk.candidate_id),
                content=content,
                rrf_score=score,
                rerank_score=score,
                metadata={
                    "document_version_id": str(chunk.document_version_id),
                    "document_type": chunk.document_type,
                    "permission_scope": chunk.permission_scope,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "retrieval_mode": "local_keyword_fallback",
                },
            )
        )
    return sorted(ranked, key=lambda item: (-item.rerank_score, item.chunk_id))[:limit]


def local_keyword_search(
    db: Session,
    *,
    query: str,
    tenant_id: str,
    permission_scopes: list[str],
    candidate_ids: list[str] | None = None,
    document_types: list[str] | None = None,
    limit: int = 8,
) -> list[RankedEvidence]:
    statement = (
        select(DocumentChunk)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(ChunkingRun, ChunkingRun.id == DocumentChunk.chunking_run_id)
        .where(
            Document.tenant_id == tenant_id,
            Document.status == "active",
            DocumentVersion.is_current.is_(True),
            Document.permission_scope.in_(permission_scopes),
            DocumentChunk.permission_scope.in_(permission_scopes),
            DocumentChunk.chunk_level == "child",
            ChunkingRun.status == ChunkingStatus.SUCCEEDED,
        )
        .order_by(ChunkingRun.created_at.desc(), DocumentChunk.position)
    )
    if candidate_ids:
        statement = statement.where(DocumentChunk.candidate_id.in_(candidate_ids))
    if document_types:
        statement = statement.where(DocumentChunk.document_type.in_(document_types))
    return rank_chunks(query, list(db.scalars(statement).all()), limit=limit)
