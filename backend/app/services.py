import hashlib
import json
import logging
import mimetypes
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.document_parsers import parse_document
from app.models import Document, DocumentVersion, EmployeeProfile, FileObject, ParseArtifact, ParseJob, ParseStatus
from app.mineru_client import parse_with_mineru
from app.object_store import ObjectStore
from app.router import ParserKind, choose_parser
from app.storage import build_object_key


logger = logging.getLogger(__name__)


def upload_document(db: Session, store: ObjectStore, *, filename: str, content: bytes, candidate_id: str, title: str, document_type: str, employee_id: UUID | None = None, knowledge_base_id: UUID | None = None, permission_scope: str = "hr_private", tenant_id: str = "course-demo") -> Document:
    logger.info(
        "document_upload_started filename=%s size_bytes=%s employee_id=%s knowledge_base_id=%s document_type=%s",
        filename,
        len(content),
        employee_id,
        knowledge_base_id,
        document_type,
    )
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    digest = hashlib.sha256(content).hexdigest()
    employee = db.get(EmployeeProfile, employee_id) if employee_id else None
    employee_no = employee.employee_no if employee else candidate_id
    document = Document(candidate_id=employee_no, employee_id=employee_id, knowledge_base_id=knowledge_base_id, tenant_id=tenant_id, title=title, document_type=document_type, permission_scope=permission_scope)
    db.add(document)
    db.flush()
    document.material_no = f"MAT-{str(document.id).split('-')[0].upper()}"
    key = build_object_key(tenant_id, str(document.id), 1, filename)
    store.put_bytes(key, content, mime)
    file_object = FileObject(bucket_name=store.settings.s3_bucket, object_key=key, original_name=filename, mime_type=mime, size_bytes=len(content), sha256=digest)
    db.add(file_object)
    db.flush()
    version = DocumentVersion(document_id=document.id, file_object_id=file_object.id, version_no=1, is_current=True)
    db.add(version)
    db.commit()
    db.refresh(document)
    logger.info(
        "document_upload_completed document_id=%s material_no=%s object_key=%s sha256=%s",
        document.id,
        document.material_no,
        key,
        digest,
    )
    return document


def parse_version(db: Session, store: ObjectStore, version_id: UUID, job_id: UUID | None = None) -> ParseJob:
    version = db.get(DocumentVersion, version_id)
    if not version:
        raise ValueError("文档版本不存在")
    file_object = db.get(FileObject, version.file_object_id)
    parser_kind = choose_parser(file_object.original_name)
    logger.info(
        "parse_job_started job_id=%s version_id=%s filename=%s parser_kind=%s",
        job_id,
        version_id,
        file_object.original_name,
        parser_kind.value,
    )
    job = db.get(ParseJob, job_id) if job_id else None
    if job is None:
        job = ParseJob(document_version_id=version.id, parser_name=parser_kind.value)
        db.add(job)
    job.parser_name = parser_kind.value
    job.status = ParseStatus.RUNNING
    job.progress = 10
    job.started_at = datetime.now(UTC)
    db.commit()
    try:
        content = store.get_bytes(file_object.object_key)
        suffix = Path(file_object.original_name).suffix
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                temp.write(content)
                temp.flush()
                temp_path = Path(temp.name)
            if parser_kind == ParserKind.MINERU:
                try:
                    result = parse_with_mineru(temp_path)
                except Exception as exc:
                    logger.warning(
                        "mineru_parse_unavailable_fallback_pypdf job_id=%s filename=%s reason=%s",
                        job.id,
                        file_object.original_name,
                        exc,
                    )
                    result = parse_document(temp_path)
            else:
                try:
                    result = parse_document(temp_path)
                except Exception as exc:
                    if suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
                        raise
                    logger.warning(
                        "basic_parser_failed_fallback_mineru job_id=%s filename=%s reason=%s",
                        job.id,
                        file_object.original_name,
                        exc,
                    )
                    result = parse_with_mineru(temp_path)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("temp_file_cleanup_failed path=%s", temp_path, exc_info=True)
        markdown = result.text.encode("utf-8")
        structured = json.dumps(result.structured or result.metadata, ensure_ascii=False, indent=2).encode("utf-8")
        base = f"artifacts/{job.id}"
        for artifact_type, key, body, content_type in [
            ("markdown", f"{base}/content.md", markdown, "text/markdown; charset=utf-8"),
            ("structured_json", f"{base}/structured.json", structured, "application/json"),
        ]:
            store.put_bytes(key, body, content_type)
            db.add(ParseArtifact(parse_job_id=job.id, artifact_type=artifact_type, bucket_name=store.settings.s3_bucket, object_key=key, content_type=content_type, size_bytes=len(body)))
            logger.info("parse_artifact_saved job_id=%s artifact_type=%s object_key=%s size_bytes=%s", job.id, artifact_type, key, len(body))
        job.parser_name = result.parser_name
        job.status = ParseStatus.SUCCEEDED
        job.progress = 100
        logger.info("parse_job_succeeded job_id=%s parser_name=%s text_chars=%s", job.id, result.parser_name, len(result.text))
    except Exception as exc:
        job.status = ParseStatus.FAILED
        job.error_code = "PARSE_FAILED"
        job.error_message = str(exc)[:2000]
        job.progress = 100
        logger.exception("parse_job_failed job_id=%s version_id=%s error=%s", job.id, version_id, exc)
    job.finished_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    logger.info("parse_job_finished job_id=%s status=%s progress=%s", job.id, job.status, job.progress)
    return job


def current_version(db: Session, document_id: UUID) -> DocumentVersion | None:
    return db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document_id, DocumentVersion.is_current.is_(True)))
