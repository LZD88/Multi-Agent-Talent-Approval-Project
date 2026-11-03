from types import SimpleNamespace
from uuid import uuid4

from app.local_evidence_search import rank_chunks


def chunk(content: str, position: int = 0):
    return SimpleNamespace(
        id=uuid4(),
        candidate_id="123456",
        document_version_id=uuid4(),
        document_type="resume",
        permission_scope="hr_private",
        page_start=position + 1,
        page_end=position + 1,
        content=content,
    )


def test_local_fallback_recalls_ai_agent_resume_content():
    results = rank_chunks(
        "AI Agent LangGraph RAG 项目经验",
        [
            chunk("负责 Agent Runtime、Tool Calling 和 LangGraph 工作流，并建设 RAG 知识库"),
            chunk("负责传统财务报表录入"),
        ],
    )

    assert len(results) == 1
    assert results[0].candidate_id == "123456"
    assert results[0].metadata["retrieval_mode"] == "local_keyword_fallback"
    assert "Agent Runtime" in results[0].content
