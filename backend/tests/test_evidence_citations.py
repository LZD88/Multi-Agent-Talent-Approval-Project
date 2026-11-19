import importlib.util
from types import SimpleNamespace as Obj
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.models import Document, DocumentVersion, DocumentChunk, ChunkingRun, ParseJob, KnowledgeBase


class MemoryRows:
    def __init__(self):
        self.rows = {}
    def add(self, cls, **kwargs):
        row = Obj(id=uuid4(), **kwargs)
        self.rows[cls, row.id] = row
        return row
    def get(self, cls, key):
        return self.rows.get((cls, key))


def fixture():
    db = MemoryRows()
    kb = db.add(KnowledgeBase, tenant_id='t1', status='active', permission_scope='hr_private')
    doc = db.add(Document, tenant_id='t1', candidate_id='C001', permission_scope='hr_private',
        status='active', title='项目复盘', knowledge_base_id=kb.id)
    version = db.add(DocumentVersion, document_id=doc.id, is_current=True, version_no=1)
    job = db.add(ParseJob, parser_version='v1')
    run = db.add(ChunkingRun, parse_job_id=job.id)
    chunk = db.add(DocumentChunk, document_version_id=version.id, candidate_id='C001',
        permission_scope='hr_private', chunking_run_id=run.id, content='负责星河项目。',
        heading_path=['职责'], markdown_start=10, markdown_end=17,
        source_locators=[], page_start=None, page_end=None, timestamp_start=None,
        timestamp_end=None, parent_chunk_id=None)
    return db, kb, doc, version, chunk


def resolve(db, chunk, tenant='t1', scopes=None):
    assert importlib.util.find_spec('app.evidence_citations'), '引用解析模块尚未实现'
    from app.evidence_citations import resolve_citation
    return resolve_citation(db, chunk.id, tenant_id=tenant,
        permission_scopes=['hr_private'] if scopes is None else scopes)


def test_citation_binds_original_version_and_markdown_offsets():
    db, kb, doc, version, chunk = fixture()
    version.is_current = False
    result = resolve(db, chunk)
    assert result['version_state'] == 'historical'
    assert result['document_version_id'] == str(version.id)
    assert result['markdown_start'] == 10
    assert result['content'] == chunk.content


def test_saved_parent_citation_derives_missing_page_range_from_pdf_locators():
    db, kb, doc, version, chunk = fixture()
    chunk.source_locators = [
        {'kind': 'page_region', 'page': 3, 'bbox': [0, 0, 100, 100]},
        {'kind': 'page_region', 'page': 1, 'bbox': [0, 0, 100, 100]},
    ]
    result = resolve(db, chunk)
    assert (result['page_start'], result['page_end']) == (1, 3)
    chunk.page_start, chunk.page_end = 2, 3
    result = resolve(db, chunk)
    assert (result['page_start'], result['page_end']) == (2, 3)


@pytest.mark.parametrize('change', ['tenant', 'document_scope', 'kb_scope', 'deleted', 'missing', 'candidate'])
def test_citation_rechecks_current_access_without_leaking_existence(change):
    db, kb, doc, version, chunk = fixture()
    if change == 'tenant': doc.tenant_id = 'other'
    if change == 'document_scope': doc.permission_scope = 'restricted'
    if change == 'kb_scope': kb.permission_scope = 'restricted'
    if change == 'deleted': doc.status = 'deleted'
    if change == 'missing': del db.rows[Document, doc.id]
    if change == 'candidate': chunk.candidate_id = 'C002'
    with pytest.raises(HTTPException) as error:
        resolve(db, chunk)
    assert error.value.status_code == 404
    assert error.value.detail == '引用不可用或无访问权限'


def test_empty_scopes_cannot_open_citation():
    db, kb, doc, version, chunk = fixture()
    with pytest.raises(HTTPException):
        resolve(db, chunk, scopes=[])


def test_citation_endpoint_rechecks_access():
    from app.main import create_app
    from app.database import get_db
    from fastapi.testclient import TestClient
    db, kb, doc, version, chunk = fixture()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    headers = {'X-Tenant-ID': 't1', 'X-Permission-Scopes': 'hr_private'}
    response = client.get(f'/api/evidence/citations/{chunk.id}', headers=headers)
    assert response.status_code == 200
    doc.permission_scope = 'restricted'
    response = client.get(f'/api/evidence/citations/{chunk.id}', headers=headers)
    assert response.status_code == 404
    assert chunk.content not in response.text


def test_citation_endpoint_returns_validated_quote_offsets():
    from app.main import create_app
    from app.database import get_db
    from fastapi.testclient import TestClient
    db, kb, doc, version, chunk = fixture()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    response = TestClient(app).get(
        f'/api/evidence/citations/{chunk.id}?quote_start=0&quote_end=6',
        headers={'X-Tenant-ID': 't1', 'X-Permission-Scopes': 'hr_private'},
    )
    assert response.status_code == 200
    assert response.json()['quote_start'] == 0
    assert response.json()['quote_end'] == 6
    assert response.json()['content'][0:6] == '负责星河项目'


@pytest.mark.parametrize('query', [
    'quote_start=0',
    'quote_end=6',
    'quote_start=-1&quote_end=6',
    'quote_start=6&quote_end=6',
    'quote_start=0&quote_end=99',
])
def test_citation_endpoint_rejects_invalid_quote_offsets(query):
    from app.main import create_app
    from app.database import get_db
    from fastapi.testclient import TestClient
    db, kb, doc, version, chunk = fixture()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    response = TestClient(app).get(
        f'/api/evidence/citations/{chunk.id}?{query}',
        headers={'X-Tenant-ID': 't1', 'X-Permission-Scopes': 'hr_private'},
    )
    assert response.status_code == 422


def test_pack_sources_reload_text_and_skip_stale_or_revoked_hits():
    from app.evidence_citations import load_pack_sources
    db, kb, doc, version, chunk = fixture()
    hit = {'chunk_id': str(chunk.id), 'candidate_id': 'C001', 'content': '不可信索引文本', 'requirement_ids': ['S1']}
    args = dict(tenant_id='t1', permission_scopes=['hr_private'])
    result = load_pack_sources(db, [hit], **args)
    assert result[0]['content'] == '负责星河项目。'
    version.is_current = False
    assert load_pack_sources(db, [hit], **args) == []
    version.is_current = True
    doc.permission_scope = 'restricted'
    assert load_pack_sources(db, [hit], **args) == []
