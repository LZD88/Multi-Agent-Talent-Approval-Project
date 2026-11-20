"""Check evidence recall after real chunking and local lexical retrieval."""
from time import monotonic
from types import SimpleNamespace

from app.chunk_service import elements_from_artifacts, infer_chunk_strategy
from app.chunking import chunk_elements
from app.local_evidence_search import rank_chunks


def run_retrieval_cases(fixture, *, chunk_size=800, chunk_overlap=100, limit=5):
    started = monotonic()
    items = []
    for case in fixture["cases"]:
        chunks = []
        for doc_no, document in enumerate(case["documents"], 1):
            elements = elements_from_artifacts(document, {})
            pieces = chunk_elements(elements, strategy=infer_chunk_strategy(elements),
                                    chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            for position, piece in enumerate(pieces, 1):
                chunks.append(SimpleNamespace(
                    id=f"{doc_no}:{position}", candidate_id="synthetic", content=piece.content,
                    document_version_id=str(doc_no), document_type="markdown", permission_scope="hr_private",
                    page_start=piece.page_start, page_end=piece.page_end,
                ))
        results = rank_chunks(case["query"], chunks, limit=limit)
        expected = case["expected_quotes"]
        missed = [quote for quote in expected if not any(quote in hit.content for hit in results)]
        items.append({
            "case_id": case["case_id"],
            "matched_quotes": len(expected) - len(missed),
            "expected_quotes": len(expected),
            "has_hits": bool(results),
            "missed_quotes": missed,
        })
    matched = sum(item["matched_quotes"] for item in items)
    expected = sum(item["expected_quotes"] for item in items)
    empty_cases = [item for item in items if not item["expected_quotes"]]
    return {
        "version": fixture["version"],
        "mode": "local_keyword_after_chunking",
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "k": limit,
        "recall_at_k": {"numerator": matched, "denominator": expected, "value": matched / expected if expected else None},
        "missing_evidence_with_hits": sum(item["has_hits"] for item in empty_cases),
        "missing_evidence_cases": len(empty_cases),
        "duration_ms": round((monotonic() - started) * 1000),
        "items": items,
    }
