"""Enterprise Knowledge Base with document ACL, collections and file ingestion."""

import hashlib
import ipaddress
import json
import logging
import mimetypes
import re
import socket
import time
import uuid
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal, get_db
from app.core.security import get_current_active_user
from app.models.models import DocumentChunk, KnowledgeDocument, User
from app.services.agent_knowledge_scope import prune_orphaned_knowledge_selectors
from app.services.document_ingestion import (
    DocumentAlreadyProcessing,
    resume_document_ingestion,
)
from app.services.document_processing_events import (
    clear_subscriber,
    mark_subscriber,
    processing_events_after,
    processing_status_payload,
    publish_processing_status,
    reset_processing_events,
    wait_for_processing_event,
    wait_for_subscriber,
)
from app.services.document_parser import DocumentParseError, extract_file_text
from app.services.knowledge_storage import (
    delete_original_file,
    read_original_file,
    save_original_file,
)
from app.services.embedding_service import calculate_content_hash
from app.services.rag_service import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    build_configured_chunks,
    hybrid_search_documents,
    ingest_document,
)
from app.services.notification_service import create_notification

router = APIRouter(prefix="/documents", tags=["Knowledge Documents"])
logger = logging.getLogger(__name__)
KB_MANAGERS = {"Owner", "Admin", "CEO", "Manager"}
VALID_DEPARTMENTS = {"BOARD", "HR", "LEGAL", "IT", "FINANCE", "SALES", "ALL"}
VALID_DOCUMENT_STATUSES = {"draft", "active", "inactive", "archived"}
VALID_CONFIDENTIALITY = {"public", "internal", "confidential", "restricted"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
VALID_DUPLICATE_STRATEGIES = {"prompt", "replace", "keep_old"}


SUBSCRIBER_WAIT_SECONDS = 2.0


def _resume_document_ingestion_background(
    record_id: uuid.UUID,
    progress_stream_id: str | None = None,
) -> None:
    """Run durable ingestion after the upload response has been sent."""
    # The uploader opens its progress stream only after this response reaches
    # the browser. Starting immediately would push the first stages into the
    # replay backlog, which the client then drains far faster than the pipeline
    # actually ran. Waiting briefly makes every transition genuinely live; the
    # bound keeps script and API uploads, which never attach, from stalling.
    wait_for_subscriber(record_id, timeout=SUBSCRIBER_WAIT_SECONDS)
    with SyncSessionLocal() as background_db:
        try:
            resume_document_ingestion(
                background_db,
                record_id,
                progress_stream_id=progress_stream_id,
            )
        except Exception:
            # resume_document_ingestion persists the failed checkpoint and error.
            logger.exception("Background ingestion failed for document %s", record_id)


def _processing_status_payload(record: KnowledgeDocument) -> dict[str, Any]:
    return processing_status_payload(record)


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _notify_indexed(db: Session, user: User, document_name: str, chunks: int) -> None:
    create_notification(
        db,
        user=user,
        event_type="DOCUMENT_READY",
        title="Tài liệu đã xử lý xong",
        message=f"{document_name}: {chunks} chunks đã được lập chỉ mục.",
        severity="SUCCESS",
        entity_type="DOCUMENT",
        entity_id=document_name,
    )
    db.commit()


class RAGSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)
    collections: Optional[list[str]] = None


class DocumentChunkResponse(BaseModel):
    id: str
    tenant_id: str
    department: str
    document_type: str
    document_id: str
    document_title: str
    document_name: str
    section_title: str
    content: str
    version: str
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    status: str
    confidentiality: str
    allowed_roles: list[str] = Field(default_factory=list)
    source_file: str
    page: Optional[int] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    content_hash: Optional[str] = None
    embedding_model: str
    embedding_version: str
    score: float
    citation_tag: str


class DocumentUpdateRequest(BaseModel):
    document_name: Optional[str] = Field(None, min_length=1, max_length=255)
    document_title: Optional[str] = Field(None, min_length=1, max_length=255)
    document_type: Optional[str] = Field(None, min_length=1, max_length=50)
    collection_name: Optional[str] = Field(None, min_length=1, max_length=100)
    department_access: Optional[str] = None
    version: Optional[str] = Field(None, min_length=1, max_length=50)
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: Optional[str] = None
    confidentiality: Optional[str] = None
    allowed_roles: Optional[list[str]] = None


class WebsiteImportRequest(BaseModel):
    url: AnyHttpUrl
    document_name: Optional[str] = Field(None, min_length=1, max_length=255)
    collection_name: str = Field(default="Website Imports", min_length=1, max_length=100)
    department_access: str = "ALL"
    document_id: Optional[str] = Field(None, min_length=1, max_length=100)
    document_title: Optional[str] = Field(None, min_length=1, max_length=255)
    document_type: str = Field(default="webpage", min_length=1, max_length=50)
    version: str = Field(default="1.0", min_length=1, max_length=50)
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: str = "active"
    confidentiality: str = "internal"
    allowed_roles: list[str] = Field(default_factory=list)


def _parse_allowed_roles(value: str | list[str] | None) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        roles = value
    else:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [item for item in value.split(",")]
        roles = decoded if isinstance(decoded, list) else [decoded]
    normalized_roles = {
        str(role).strip().strip("[]'\"").strip().lower()
        for role in roles
        if str(role).strip().strip("[]'\"").strip()
    }
    return sorted(normalized_roles)


def _governance_metadata(
    *,
    status: str,
    confidentiality: str,
    allowed_roles: str | list[str] | None,
    effective_date: date | None,
    expiration_date: date | None,
) -> dict[str, Any]:
    normalized_status = status.strip().lower()
    if normalized_status not in VALID_DOCUMENT_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported document status")
    normalized_confidentiality = confidentiality.strip().lower()
    if normalized_confidentiality not in VALID_CONFIDENTIALITY:
        raise HTTPException(status_code=422, detail="Unsupported confidentiality")
    if effective_date and expiration_date and expiration_date < effective_date:
        raise HTTPException(
            status_code=422,
            detail="expiration_date must be on or after effective_date",
        )
    normalized_roles = _parse_allowed_roles(allowed_roles)
    if normalized_confidentiality == "restricted" and not normalized_roles:
        raise HTTPException(
            status_code=422,
            detail="restricted documents require at least one allowed role",
        )
    return {
        "status": normalized_status,
        "confidentiality": normalized_confidentiality,
        "allowed_roles": normalized_roles,
        "effective_date": effective_date,
        "expiration_date": expiration_date,
    }


def _duplicate_chunk_report(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    document_id: str,
    version: str,
    content: str,
    chunking_config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return exact content-hash matches for the same logical document version."""
    config = chunking_config or {}
    incoming_chunks = build_configured_chunks(
        content,
        mode=str(config.get("mode", "standard")),
        chunk_size=int(config.get("chunk_size", CHUNK_SIZE_TOKENS)),
        chunk_overlap=int(config.get("chunk_overlap", CHUNK_OVERLAP_TOKENS)),
        parent_chunk_size=int(config.get("parent_chunk_size", 1024)),
    )
    incoming_by_hash: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, chunk in enumerate(incoming_chunks):
        content_hash = calculate_content_hash(chunk["content"])
        incoming_by_hash.setdefault(content_hash, []).append((index, chunk))

    if not incoming_by_hash:
        return [], len(incoming_chunks)

    existing_chunks = (
        db.query(DocumentChunk)
        .filter(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_id == document_id,
            DocumentChunk.version == version,
            DocumentChunk.content_hash.in_(list(incoming_by_hash)),
        )
        .order_by(DocumentChunk.created_at.desc())
        .all()
    )
    existing_by_hash: dict[str, DocumentChunk] = {}
    for chunk in existing_chunks:
        if chunk.content_hash:
            existing_by_hash.setdefault(chunk.content_hash, chunk)

    duplicates: list[dict[str, Any]] = []
    for content_hash, incoming in incoming_by_hash.items():
        existing = existing_by_hash.get(content_hash)
        if not existing:
            continue
        for incoming_index, new_chunk in incoming:
            duplicates.append({
                "content_hash": content_hash,
                "content": new_chunk["content"],
                "incoming": {
                    "chunk_index": incoming_index,
                    "section_title": new_chunk["section_title"],
                    "page_start": new_chunk["page"],
                    "page_end": max(new_chunk["pages"]) if new_chunk["pages"] else new_chunk["page"],
                },
                "existing": {
                    "chunk_id": str(existing.id),
                    "chunk_index": existing.chunk_index,
                    "section_title": existing.section_title or "Untitled section",
                    "page_start": existing.page_start or existing.page,
                    "page_end": existing.page_end or existing.page,
                    "created_at": existing.created_at.isoformat() if existing.created_at else None,
                },
            })
    duplicates.sort(key=lambda item: item["incoming"]["chunk_index"])
    return duplicates, len(incoming_chunks)


def _set_document_processing_status(
    db: Session,
    *,
    user: User,
    document_id: str,
    file_name: str,
    document_title: str,
    department: str,
    document_type: str,
    version: str,
    governance: dict[str, Any],
    processing_status: str,
    processing_progress: int = 0,
    collection_name: str = "General Knowledge",
    reset_attempts: bool = False,
    storage_key: str | None = None,
    source_hash: str | None = None,
    source_url: str | None = None,
    error_message: str | None = None,
    chunking_config: dict[str, Any] | None = None,
) -> KnowledgeDocument:
    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == user.tenant_id,
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == version,
    ).first()
    if not record:
        record = KnowledgeDocument(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            document_id=document_id,
            created_by_id=user.id,
        )
        db.add(record)
    record.file_name = file_name
    record.document_title = document_title
    record.collection_name = collection_name
    record.department = department
    record.document_type = document_type
    record.version = version
    record.status = governance["status"]
    record.processing_status = processing_status
    record.processing_progress = max(0, min(100, processing_progress))
    if reset_attempts:
        record.processing_attempts = 0
        record.processing_checkpoint = "uploaded"
        record.parsed_text = None
    record.confidentiality = governance["confidentiality"]
    record.allowed_roles = governance["allowed_roles"]
    record.effective_date = governance["effective_date"]
    record.expiration_date = governance["expiration_date"]
    record.storage_key = storage_key or record.storage_key
    record.source_hash = source_hash or record.source_hash
    record.source_url = source_url
    if chunking_config is not None:
        record.chunking_config = chunking_config
    record.error_message = error_message
    db.commit()
    if reset_attempts:
        reset_processing_events(record.id)
    publish_processing_status(record)
    return record


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0
        self._heading_prefix: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")
            self._heading_prefix = f"{'#' * int(tag[1])} "
        elif tag in {"p", "div", "section", "article", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_prefix = None
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            text = data.strip()
            if self._heading_prefix:
                text = f"{self._heading_prefix}{text}"
                self._heading_prefix = None
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(part for part in self.parts if part.strip())


def _validate_management(current_user: User, department_access: str) -> str:
    if current_user.role not in KB_MANAGERS:
        raise HTTPException(status_code=403, detail="Insufficient permission to manage knowledge")
    department = department_access.upper()
    if department not in VALID_DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Unsupported department_access")
    if current_user.role == "Manager" and department not in {
        current_user.department, "ALL"
    }:
        raise HTTPException(status_code=403, detail="Manager cannot publish to another department")
    return department


def _visible_chunks_query(db: Session, current_user: User):
    query = db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == current_user.tenant_id
    )
    if current_user.role in {"Owner", "Admin", "CEO"}:
        return query
    return query.filter(
        DocumentChunk.department_access.in_(("ALL", current_user.department))
    )


def _chunk_visible_to_user(chunk: DocumentChunk, current_user: User) -> bool:
    if current_user.role in {"Owner", "Admin", "CEO"}:
        return True
    today = date.today()
    if chunk.status != "active":
        return False
    if chunk.effective_date and chunk.effective_date > today:
        return False
    if chunk.expiration_date and chunk.expiration_date < today:
        return False
    principals = {current_user.role.lower(), current_user.department.lower()}
    allowed_roles = {str(role).lower() for role in (chunk.allowed_roles or [])}
    if allowed_roles and not principals.intersection(allowed_roles):
        return False
    return chunk.confidentiality != "restricted" or bool(allowed_roles)


def _document_visible_to_user(document: KnowledgeDocument, current_user: User) -> bool:
    if current_user.role in {"Owner", "Admin", "CEO"}:
        return True
    if document.department not in {"ALL", current_user.department}:
        return False
    if current_user.role == "Manager" and document.processing_status != "ready":
        return document.created_by_id == current_user.id
    today = date.today()
    if document.status != "active" or document.processing_status != "ready":
        return False
    if document.effective_date and document.effective_date > today:
        return False
    if document.expiration_date and document.expiration_date < today:
        return False
    principals = {current_user.role.lower(), current_user.department.lower()}
    allowed_roles = {str(role).lower() for role in (document.allowed_roles or [])}
    return not allowed_roles or bool(principals.intersection(allowed_roles))


def _visible_document_reader_source(
    db: Session,
    current_user: User,
    document_id: str,
    version: str,
) -> tuple[KnowledgeDocument | None, list[DocumentChunk]]:
    """Resolve a readable document without leaking inaccessible IDs."""
    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == version,
    ).first()
    if record and not _document_visible_to_user(record, current_user):
        raise HTTPException(status_code=404, detail="Document not found")

    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == current_user.tenant_id,
        or_(
            DocumentChunk.document_id == document_id,
            DocumentChunk.document_name == document_id,
        ),
        DocumentChunk.version == version,
    ).order_by(DocumentChunk.chunk_index).all()
    visible_chunks = [
        chunk
        for chunk in chunks
        if chunk.department_access in {"ALL", current_user.department}
        or current_user.role in {"Owner", "Admin", "CEO"}
        if _chunk_visible_to_user(chunk, current_user)
    ]
    if not record and not visible_chunks:
        raise HTTPException(status_code=404, detail="Document not found")
    return record, visible_chunks


def _extract_file_text(filename: str, data: bytes) -> str:
    try:
        return extract_file_text(filename, data)
    except DocumentParseError as exc:
        message = str(exc)
        status_code = 415 if message.startswith("Supported file types") else 422
        if message == "PDF parser is not installed":
            status_code = 503
        raise HTTPException(status_code=status_code, detail=message) from exc


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Only public HTTP(S) URLs are allowed")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
        }
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail="Website hostname cannot be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise HTTPException(status_code=422, detail="Private or reserved network URLs are blocked")


def _download_public_html(initial_url: str) -> tuple[str, str]:
    current_url = initial_url
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        for _ in range(4):
            _validate_public_url(current_url)
            with client.stream("GET", current_url, headers={"User-Agent": "AI-Workforce-KB/1.0"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise HTTPException(status_code=422, detail="Website redirect is missing Location")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code >= 400:
                    raise HTTPException(status_code=422, detail=f"Website returned HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    raise HTTPException(status_code=415, detail="Website did not return HTML content")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 5 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="Website exceeds the 5 MB import limit")
                    chunks.append(chunk)
                return current_url, b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    raise HTTPException(status_code=422, detail="Website has too many redirects")


@router.get("/", summary="List visible knowledge documents")
def list_documents(
    mine: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[dict[str, Any]]:
    chunks = [
        chunk
        for chunk in _visible_chunks_query(db, current_user).order_by(
            DocumentChunk.created_at.desc()
        ).all()
        if _chunk_visible_to_user(chunk, current_user)
    ]
    record_query = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
    )
    if mine:
        record_query = record_query.filter(KnowledgeDocument.created_by_id == current_user.id)
    if current_user.role in KB_MANAGERS:
        record_query = record_query.filter(or_(
            KnowledgeDocument.status == "active",
            KnowledgeDocument.processing_status != "ready",
        ))
    else:
        record_query = record_query.filter(KnowledgeDocument.status == "active")
    records = [
        document
        for document in record_query.order_by(KnowledgeDocument.created_at.desc()).all()
        if _document_visible_to_user(document, current_user)
    ]
    docs: dict[str, dict[str, Any]] = {
        document.document_id: {
            "document_id": document.document_id,
            "document_name": document.file_name,
            "document_title": document.document_title,
            "document_type": document.document_type,
            "version": document.version,
            "collection_name": document.collection_name,
            "department_access": document.department,
            "effective_date": (
                document.effective_date.isoformat() if document.effective_date else None
            ),
            "expiration_date": (
                document.expiration_date.isoformat() if document.expiration_date else None
            ),
            "document_status": document.status,
            "processing_status": document.processing_status,
            "processing_checkpoint": document.processing_checkpoint,
            "processing_progress": document.processing_progress,
            "processing_attempts": document.processing_attempts,
            "confidentiality": document.confidentiality,
            "allowed_roles": document.allowed_roles or [],
            "source_file": document.file_name,
            "source_url": document.source_url,
            "storage_key": document.storage_key,
            "chunking_config": document.chunking_config,
            "chunk_count": document.chunk_count,
            "status": document.processing_status.upper(),
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "error_message": document.error_message,
        }
        for document in records
    }
    record_keys = set(docs)
    if mine:
        return list(docs.values())
    for chunk in chunks:
        key = chunk.document_id or chunk.document_name
        item = docs.setdefault(key, {
            "document_id": key,
            "document_name": chunk.document_name,
            "document_title": chunk.document_title or chunk.document_name,
            "document_type": chunk.document_type,
            "version": chunk.version,
            "collection_name": chunk.collection_name,
            "department_access": chunk.department_access,
            "effective_date": chunk.effective_date.isoformat() if chunk.effective_date else None,
            "expiration_date": chunk.expiration_date.isoformat() if chunk.expiration_date else None,
            "document_status": chunk.status,
            "confidentiality": chunk.confidentiality,
            "allowed_roles": chunk.allowed_roles or [],
            "source_file": chunk.source_file or chunk.document_name,
            "chunk_count": 0,
            "status": "INDEXED",
            "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
        })
        if key not in record_keys:
            item["chunk_count"] += 1
    return list(docs.values())


@router.get("/{document_id}/reader", summary="Read an ACL-filtered knowledge document")
def read_document(
    document_id: str,
    version: str = "1.0",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    record, chunks = _visible_document_reader_source(
        db, current_user, document_id, version
    )
    content = (record.parsed_text or "").strip() if record else ""
    if not content:
        content = "\n\n".join(
            chunk.content.strip() for chunk in chunks if chunk.content.strip()
        )
    if not content:
        raise HTTPException(status_code=422, detail="Document has no readable content")
    first_chunk = chunks[0] if chunks else None
    document_name = (
        record.file_name
        if record
        else first_chunk.document_name
        if first_chunk
        else document_id
    )
    return {
        "document_id": document_id,
        "document_name": document_name,
        "document_title": (
            record.document_title
            if record
            else first_chunk.document_title or document_name
        ),
        "document_type": (
            record.document_type if record else first_chunk.document_type
        ),
        "version": version,
        "content": content,
        "character_count": len(content),
        "chunk_count": len(chunks),
        "processing_status": record.processing_status if record else "ready",
        "processing_checkpoint": record.processing_checkpoint if record else "ready",
        "processing_progress": record.processing_progress if record else 100,
        "error_message": record.error_message if record else None,
        "chunking_config": record.chunking_config if record else None,
        "chunks": [
            {
                "id": str(chunk.id),
                "chunk_index": chunk.chunk_index,
                "section_title": chunk.section_title,
                "content": chunk.content,
                "page_start": chunk.page_start or chunk.page,
                "page_end": chunk.page_end or chunk.page,
                "token_count": (chunk.metadata_ or {}).get("token_count"),
                "chunking_mode": (chunk.metadata_ or {}).get("chunking_mode", "standard"),
                "parent_chunk_index": (chunk.metadata_ or {}).get("parent_chunk_index"),
                "parent_content": (chunk.metadata_ or {}).get("parent_content"),
            }
            for chunk in chunks
        ],
        "source_url": record.source_url if record else None,
        "download_url": (
            f"/api/v1/documents/{quote(document_id, safe='')}/download?version={quote(version, safe='')}"
            if record and record.storage_key
            else None
        ),
    }


@router.get("/{document_id}/download", summary="Download an authorized original knowledge file")
def download_document(
    document_id: str,
    version: str = "1.0",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Response:
    record, _ = _visible_document_reader_source(
        db, current_user, document_id, version
    )
    if not record or not record.storage_key:
        raise HTTPException(status_code=404, detail="Original file is not available")
    try:
        content = read_original_file(record.storage_key)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Original file is not available") from exc
    filename = Path(record.file_name).name
    ascii_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "document"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_filename}\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get(
    "/processing-status/{document_id}",
    summary="Get the current document processing stage",
)
def get_document_processing_status(
    document_id: str,
    version: str = "1.0",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    """Expose committed ingestion stages so the uploader can show real progress."""
    if current_user.role not in KB_MANAGERS:
        raise HTTPException(status_code=403, detail="Insufficient permission to manage knowledge")

    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == version,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Document processing status not found")
    if current_user.role == "Manager" and record.department not in {
        "ALL",
        current_user.department,
    }:
        raise HTTPException(status_code=404, detail="Document processing status not found")

    return _processing_status_payload(record)


@router.get(
    "/processing-events/{document_id}",
    summary="Stream document processing stages in real time",
)
def stream_document_processing_events(
    document_id: str,
    version: str = "1.0",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    """Stream committed pipeline transitions and terminate at ready or failed."""
    if current_user.role not in KB_MANAGERS:
        raise HTTPException(status_code=403, detail="Insufficient permission to manage knowledge")

    initial = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == version,
    ).first()
    if not initial or (
        current_user.role == "Manager"
        and initial.department not in {"ALL", current_user.department}
    ):
        raise HTTPException(status_code=404, detail="Document processing status not found")

    tenant_id = current_user.tenant_id
    record_id = initial.id
    manager_department = current_user.department if current_user.role == "Manager" else None

    # Registering here, before the lazy generator runs, is what releases an
    # upload waiting in `_resume_document_ingestion_background`.
    mark_subscriber(record_id)
    # Snapshots already queued when this stream opened are history, not live
    # pipeline activity. Naming them apart lets the client jump straight to the
    # current stage instead of animating through stages that already finished.
    backlog = processing_events_after(record_id, 0)
    replay_through = backlog[-1][0] if backlog else 0

    def events():
        deadline = time.monotonic() + 15 * 60
        last_payload: str | None = None
        heartbeat_at = time.monotonic()
        event_sequence = 0
        try:
            while time.monotonic() < deadline:
                queued_events = processing_events_after(record_id, event_sequence)
                if queued_events:
                    for event_sequence, payload in queued_events:
                        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                        if serialized == last_payload:
                            continue
                        status = str(payload["processing_status"])
                        if status in {"ready", "failed"}:
                            event = status
                        elif event_sequence <= replay_through:
                            event = "replay"
                        else:
                            event = "status"
                        yield _sse_event(event, payload)
                        last_payload = serialized
                        heartbeat_at = time.monotonic()
                        if status in {"ready", "failed"}:
                            return
                    continue

                # Database polling remains a cross-process/restart fallback. Live
                # ingestion in this process is delivered by the ordered queue above.
                with SyncSessionLocal() as event_db:
                    record = event_db.query(KnowledgeDocument).filter(
                        KnowledgeDocument.tenant_id == tenant_id,
                        KnowledgeDocument.document_id == document_id,
                        KnowledgeDocument.version == version,
                    ).first()
                    if not record or (
                        manager_department is not None
                        and record.department not in {"ALL", manager_department}
                    ):
                        yield _sse_event("error", {
                            "code": "DOCUMENT_NOT_FOUND",
                            "message": "Document processing status not found",
                        })
                        return
                    payload = _processing_status_payload(record)

                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if serialized != last_payload:
                    status = str(payload["processing_status"])
                    event = status if status in {"ready", "failed"} else "status"
                    yield _sse_event(event, payload)
                    last_payload = serialized
                    heartbeat_at = time.monotonic()
                    if status in {"ready", "failed"}:
                        return
                elif time.monotonic() - heartbeat_at >= 10:
                    yield ": keep-alive\n\n"
                    heartbeat_at = time.monotonic()
                wait_for_processing_event(record_id, event_sequence, timeout=0.5)

            yield _sse_event("error", {
                "code": "PIPELINE_STREAM_TIMEOUT",
                "message": "Document processing stream timed out",
            })
        finally:
            clear_subscriber(record_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/{document_id}/retry",
    summary="Resume document ingestion from its last completed checkpoint",
)
def retry_document_ingestion(
    document_id: str,
    version: str = "1.0",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    if current_user.role not in KB_MANAGERS:
        raise HTTPException(status_code=403, detail="Insufficient permission to manage knowledge")
    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == version,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.role == "Manager" and record.department not in {
        "ALL",
        current_user.department,
    }:
        raise HTTPException(status_code=403, detail="Manager cannot retry this document")
    if record.processing_status == "ready":
        return {
            "success": True,
            "document_id": record.document_id,
            "version": record.version,
            "processing_status": "ready",
            "processing_checkpoint": "ready",
            "chunks_created": record.chunk_count,
        }
    if not record.storage_key:
        raise HTTPException(status_code=409, detail="Original document is not available")

    try:
        reset_processing_events(record.id)
        chunks = resume_document_ingestion(db, record.id)
    except DocumentAlreadyProcessing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "document_id": record.document_id,
        "version": record.version,
        "processing_status": "ready",
        "processing_checkpoint": "ready",
        "chunks_created": len(chunks),
    }


@router.post("/search", response_model=list[DocumentChunkResponse], summary="Search knowledge")
def search_rag(
    req: RAGSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[DocumentChunkResponse]:
    results = hybrid_search_documents(
        db=db,
        tenant_id=current_user.tenant_id,
        query_text=req.query,
        department=(
            "*" if current_user.role in {"Owner", "Admin", "CEO"}
            else current_user.department
        ),
        top_k=req.top_k,
        collections=req.collections,
        user_role=current_user.role,
        user_department=current_user.department,
    )
    return [DocumentChunkResponse(**result) for result in results]


@router.post("/ingest-text", summary="Ingest text into a collection")
def ingest_text_document(
    document_name: str = Form(...),
    content: str = Form(...),
    department_access: str = Form("ALL"),
    collection_name: str = Form("General Knowledge"),
    document_id: Optional[str] = Form(None),
    document_title: Optional[str] = Form(None),
    document_type: str = Form("knowledge"),
    version: str = Form("1.0"),
    effective_date: Optional[date] = Form(None),
    expiration_date: Optional[date] = Form(None),
    status: str = Form("active"),
    confidentiality: str = Form("internal"),
    allowed_roles: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    department = _validate_management(current_user, department_access)
    if not document_name.strip() or not content.strip():
        raise HTTPException(status_code=400, detail="Document name and content are required")
    governance = _governance_metadata(
        status=status,
        confidentiality=confidentiality,
        allowed_roles=allowed_roles,
        effective_date=effective_date,
        expiration_date=expiration_date,
    )
    resolved_document_id = document_id.strip() if document_id else document_name.strip()
    chunks = ingest_document(
        db=db,
        tenant_id=current_user.tenant_id,
        document_name=document_name.strip(),
        content=content,
        department_access=department,
        collection_name=collection_name.strip() or "General Knowledge",
        document_id=resolved_document_id,
        document_title=document_title.strip() if document_title else document_name.strip(),
        document_type=document_type.strip().lower(),
        version=version.strip(),
        source_file=document_name.strip(),
        created_by_id=current_user.id,
        **governance,
    )
    _notify_indexed(db, current_user, document_name.strip(), len(chunks))
    return {
        "success": True,
        "document_id": resolved_document_id,
        "document_name": document_name.strip(),
        "status": "INDEXED",
        "processing_status": "ready",
        "embedding_model": chunks[0].embedding_model if chunks else None,
        "chunks_created": len(chunks),
    }


@router.post("/preview-chunks", summary="Preview chunks before indexing")
def preview_document_chunks(
    file: UploadFile = File(...),
    chunking_mode: str = Form("standard"),
    chunk_size: int = Form(CHUNK_SIZE_TOKENS),
    chunk_overlap: int = Form(CHUNK_OVERLAP_TOKENS),
    parent_chunk_size: int = Form(1024),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    if current_user.role not in KB_MANAGERS:
        raise HTTPException(status_code=403, detail="Insufficient permission to manage knowledge")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
    filename = (file.filename or "document").strip()
    content = _extract_file_text(filename, data).strip()
    if not content:
        raise HTTPException(status_code=422, detail="No readable text found in the file")
    try:
        chunks = build_configured_chunks(
            content,
            mode=chunking_mode,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            parent_chunk_size=parent_chunk_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "document_name": filename,
        "character_count": len(content),
        "estimated_chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_index": index,
                "section_title": chunk["section_title"],
                "content": chunk["content"],
                "token_count": chunk["token_count"],
                "page_start": chunk["page"],
                "page_end": max(chunk["pages"]) if chunk["pages"] else chunk["page"],
                "chunking_mode": chunk.get("chunking_mode", "standard"),
                "parent_chunk_index": chunk.get("parent_chunk_index"),
                "parent_content": chunk.get("parent_content"),
            }
            for index, chunk in enumerate(chunks[:20])
        ],
    }


@router.post("/upload", status_code=201, summary="Upload PDF, DOCX, TXT or CSV")
def upload_document(
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    department_access: str = Form("ALL"),
    collection_name: str = Form("General Knowledge"),
    document_id: Optional[str] = Form(None),
    document_title: Optional[str] = Form(None),
    document_type: str = Form("knowledge"),
    version: str = Form("1.0"),
    effective_date: Optional[date] = Form(None),
    expiration_date: Optional[date] = Form(None),
    status: str = Form("active"),
    confidentiality: str = Form("internal"),
    allowed_roles: str = Form(""),
    duplicate_strategy: str = Form("prompt"),
    chunking_mode: str = Form("standard"),
    chunk_size: int = Form(CHUNK_SIZE_TOKENS),
    chunk_overlap: int = Form(CHUNK_OVERLAP_TOKENS),
    parent_chunk_size: int = Form(1024),
    async_processing: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    department = _validate_management(current_user, department_access)
    normalized_duplicate_strategy = duplicate_strategy.strip().lower()
    if normalized_duplicate_strategy not in VALID_DUPLICATE_STRATEGIES:
        raise HTTPException(status_code=422, detail="Unsupported duplicate strategy")
    # Validate duplicates before persisting a new durable pipeline record.
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
    filename = (file.filename or "document").strip()
    governance = _governance_metadata(
        status=status,
        confidentiality=confidentiality,
        allowed_roles=allowed_roles,
        effective_date=effective_date,
        expiration_date=expiration_date,
    )
    resolved_document_id = document_id.strip() if document_id else filename
    resolved_title = document_title.strip() if document_title else filename
    resolved_type = document_type.strip().lower()
    resolved_version = version.strip()
    in_progress_record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == resolved_document_id,
        KnowledgeDocument.version == resolved_version,
        KnowledgeDocument.processing_status.in_(
            ("uploaded", "parsing", "chunking", "embedding", "indexing")
        ),
    ).first()
    if in_progress_record:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DOCUMENT_PROCESSING",
                "message": "This document version is already being processed",
                "document_id": resolved_document_id,
                "version": resolved_version,
                "processing_status": in_progress_record.processing_status,
                "processing_progress": in_progress_record.processing_progress,
            },
        )
    content = _extract_file_text(filename, data).strip()
    if not content:
        raise HTTPException(status_code=422, detail="No readable text found in the file")
    chunking_config = {
        "mode": chunking_mode.strip().lower(),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "parent_chunk_size": parent_chunk_size,
    }
    if chunking_config["mode"] not in {"standard", "parent_child"}:
        raise HTTPException(
            status_code=422,
            detail="mode must be either 'standard' or 'parent_child'",
        )
    if chunk_size <= 0:
        raise HTTPException(status_code=422, detail="chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise HTTPException(
            status_code=422,
            detail="chunk_overlap must be between zero and chunk_size - 1",
        )
    if parent_chunk_size <= 0:
        raise HTTPException(
            status_code=422,
            detail="parent_chunk_size must be greater than zero",
        )
    if chunking_config["mode"] == "parent_child" and parent_chunk_size < chunk_size:
        raise HTTPException(
            status_code=422,
            detail="parent_chunk_size must be greater than or equal to chunk_size",
        )

    # A new logical version cannot contain duplicate stored chunks. Avoid
    # calling the AI chunk endpoint during the upload acknowledgement; the
    # background ingestion will make the single authoritative call and expose
    # its response through processing-events.
    has_existing_chunks = db.query(DocumentChunk.id).filter(
        DocumentChunk.tenant_id == current_user.tenant_id,
        DocumentChunk.document_id == resolved_document_id,
        DocumentChunk.version == resolved_version,
    ).first() is not None
    existing_record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == resolved_document_id,
        KnowledgeDocument.version == resolved_version,
    ).first()
    existing_chunk_count = (
        db.query(DocumentChunk.id).filter(
            DocumentChunk.tenant_id == current_user.tenant_id,
            DocumentChunk.document_id == resolved_document_id,
            DocumentChunk.version == resolved_version,
        ).count()
        if has_existing_chunks
        else 0
    )
    exact_source_duplicate = bool(
        async_processing
        and existing_record
        and existing_record.source_hash
        and existing_record.source_hash == hashlib.sha256(data).hexdigest()
    )
    duplicates: list[dict[str, Any]] = []
    incoming_chunk_count = existing_chunk_count if exact_source_duplicate else 0
    # Async uploads must acknowledge before invoking the AI service. Exact
    # re-uploads are detected from the durable source hash; partial chunk
    # comparison remains available to the synchronous management endpoint.
    if has_existing_chunks and not async_processing:
        try:
            duplicates, incoming_chunk_count = _duplicate_chunk_report(
                db,
                tenant_id=current_user.tenant_id,
                document_id=resolved_document_id,
                version=resolved_version,
                content=content,
                chunking_config=chunking_config,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if (
        (duplicates or exact_source_duplicate)
        and normalized_duplicate_strategy == "prompt"
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DUPLICATE_CHUNKS",
                "message": "Tài liệu có các chunk trùng với phiên bản đang được lưu.",
                "document_id": resolved_document_id,
                "document_name": filename,
                "version": resolved_version,
                "duplicate_count": len(duplicates) or existing_chunk_count,
                "incoming_chunk_count": incoming_chunk_count,
                "duplicates": duplicates,
                "actions": ["replace", "keep_old"],
            },
        )
    if (
        has_existing_chunks
        and normalized_duplicate_strategy == "keep_old"
        and (duplicates or exact_source_duplicate)
    ):
        response.status_code = 200
        return {
            "success": True,
            "document_id": resolved_document_id,
            "document_name": filename,
            "status": "KEPT_EXISTING",
            "processing_status": (
                existing_record.processing_status if existing_record else "ready"
            ),
            "processing_progress": 100,
            "chunks_created": 0,
            "duplicate_count": len(duplicates) or existing_chunk_count,
        }

    try:
        storage_key, source_hash = save_original_file(
            tenant_id=current_user.tenant_id,
            document_id=resolved_document_id,
            version=resolved_version,
            filename=filename,
            data=data,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not store uploaded file") from exc
    record = _set_document_processing_status(
        db,
        user=current_user,
        document_id=resolved_document_id,
        file_name=filename,
        document_title=resolved_title,
        department=department,
        document_type=resolved_type,
        version=resolved_version,
        governance=governance,
        processing_status="uploaded",
        processing_progress=0,
        collection_name=collection_name.strip() or "General Knowledge",
        reset_attempts=True,
        storage_key=storage_key,
        source_hash=source_hash,
        chunking_config=chunking_config,
    )
    if async_processing:
        progress_stream_id = uuid.uuid4().hex
        background_tasks.add_task(
            _resume_document_ingestion_background,
            record.id,
            progress_stream_id,
        )
        response.status_code = 202
        return {
            "success": True,
            "document_id": resolved_document_id,
            "document_name": filename,
            "version": resolved_version,
            "status": "ACCEPTED",
            "processing_status": "uploaded",
            "processing_checkpoint": "uploaded",
            "processing_progress": 0,
            "storage_key": storage_key,
            "embedding_model": None,
            "chunks_created": 0,
            "processing_stream_id": progress_stream_id,
        }

    chunks = resume_document_ingestion(db, record.id)
    return {
        "success": True,
        "document_id": resolved_document_id,
        "document_name": filename,
        "version": resolved_version,
        "status": "INDEXED",
        "processing_status": "ready",
        "processing_checkpoint": "ready",
        "processing_progress": 100,
        "storage_key": storage_key,
        "embedding_model": chunks[0].embedding_model if chunks else None,
        "chunks_created": len(chunks),
    }


@router.post("/import-website", status_code=201, summary="Import a public website")
def import_website(
    req: WebsiteImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    department = _validate_management(current_user, req.department_access)
    final_url, html = _download_public_html(str(req.url))
    name = req.document_name or urlparse(final_url).hostname or "website"
    governance = _governance_metadata(
        status=req.status,
        confidentiality=req.confidentiality,
        allowed_roles=req.allowed_roles,
        effective_date=req.effective_date,
        expiration_date=req.expiration_date,
    )
    resolved_document_id = req.document_id or name
    storage_key, source_hash = save_original_file(
        tenant_id=current_user.tenant_id,
        document_id=resolved_document_id,
        version=req.version,
        filename=f"{name}.html",
        data=html.encode("utf-8"),
    )
    record = _set_document_processing_status(
        db,
        user=current_user,
        document_id=resolved_document_id,
        file_name=f"{name}.html",
        document_title=req.document_title or name,
        department=department,
        document_type=req.document_type.strip().lower(),
        version=req.version.strip(),
        governance=governance,
        processing_status="parsing",
        storage_key=storage_key,
        source_hash=source_hash,
        source_url=final_url,
    )
    try:
        parser = _HTMLTextExtractor()
        parser.feed(html)
        content = parser.text().strip()
        if not content:
            raise HTTPException(status_code=422, detail="No readable text found on website")
    except Exception as exc:
        record.processing_status = "failed"
        record.error_message = str(exc)[:2000]
        db.commit()
        raise
    chunks = ingest_document(
        db=db,
        tenant_id=current_user.tenant_id,
        document_name=name,
        content=content,
        department_access=department,
        collection_name=req.collection_name,
        source_metadata={"source_type": "WEBSITE", "source_url": final_url},
        document_id=resolved_document_id,
        document_title=req.document_title or name,
        document_type=req.document_type.strip().lower(),
        version=req.version.strip(),
        source_file=final_url,
        storage_key=storage_key,
        source_hash=source_hash,
        source_url=final_url,
        created_by_id=current_user.id,
        **governance,
    )
    _notify_indexed(db, current_user, name, len(chunks))
    return {
        "success": True,
        "document_id": resolved_document_id,
        "document_name": name,
        "source_url": final_url,
        "status": "INDEXED",
        "processing_status": "ready",
        "storage_key": storage_key,
        "embedding_model": chunks[0].embedding_model if chunks else None,
        "chunks_created": len(chunks),
    }


@router.patch("/{document_id}", summary="Update document metadata")
def update_document(
    document_id: str,
    req: DocumentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    if current_user.role not in KB_MANAGERS:
        raise HTTPException(status_code=403, detail="Insufficient permission to manage knowledge")
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == current_user.tenant_id,
        or_(
            DocumentChunk.document_id == document_id,
            DocumentChunk.document_name == document_id,
        ),
    ).all()
    records = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == document_id,
    ).all()
    if not chunks and not records:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.role == "Manager" and any(
        chunk.department_access not in {"ALL", current_user.department} for chunk in chunks
    ):
        raise HTTPException(status_code=403, detail="Manager cannot edit this document")
    data = req.model_dump(exclude_unset=True)
    if "version" in data:
        raise HTTPException(
            status_code=409,
            detail="Upload a new document version instead of editing version in place",
        )
    if "department_access" in data:
        data["department_access"] = _validate_management(
            current_user, data["department_access"]
        )
    governance_fields = {
        "status",
        "confidentiality",
        "allowed_roles",
        "effective_date",
        "expiration_date",
    }
    if governance_fields.intersection(data):
        governance_source = chunks[0] if chunks else records[0]
        governance = _governance_metadata(
            status=data.get("status") or governance_source.status,
            confidentiality=data.get(
                "confidentiality", governance_source.confidentiality
            ),
            allowed_roles=data.get("allowed_roles", governance_source.allowed_roles),
            effective_date=data.get("effective_date", governance_source.effective_date),
            expiration_date=data.get(
                "expiration_date", governance_source.expiration_date
            ),
        )
        data.update(governance)
    for chunk in chunks:
        for field_name, value in data.items():
            setattr(chunk, field_name, value)
        metadata = dict(chunk.metadata_ or {})
        for field_name in data.keys() & {
            "document_title",
            "document_type",
            "version",
            "effective_date",
            "expiration_date",
            "status",
            "confidentiality",
            "allowed_roles",
        }:
            value = data[field_name]
            metadata[field_name] = value.isoformat() if isinstance(value, date) else value
        chunk.metadata_ = metadata
    record_field_map = {
        "document_name": "file_name",
        "document_title": "document_title",
        "document_type": "document_type",
        "department_access": "department",
        "effective_date": "effective_date",
        "expiration_date": "expiration_date",
        "status": "status",
        "confidentiality": "confidentiality",
        "allowed_roles": "allowed_roles",
    }
    for record in records:
        for field_name, value in data.items():
            record_field = record_field_map.get(field_name)
            if record_field:
                setattr(record, record_field, value)
    db.commit()
    return {"message": "Document updated successfully", "chunks_updated": len(chunks)}


@router.delete("/{document_id}", summary="Delete a document and all of its chunks")
def delete_document(
    document_id: str,
    version: str = "1.0",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    if current_user.role not in KB_MANAGERS:
        raise HTTPException(status_code=403, detail="Insufficient permission to manage knowledge")

    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == current_user.tenant_id,
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == version,
    ).first()
    legacy_identity = (
        DocumentChunk.knowledge_document_id.is_(None),
        DocumentChunk.tenant_id == current_user.tenant_id,
        or_(
            DocumentChunk.document_id == document_id,
            DocumentChunk.document_name == document_id,
        ),
        DocumentChunk.version == version,
    )
    if record:
        chunk_query = db.query(DocumentChunk).filter(
            DocumentChunk.tenant_id == current_user.tenant_id,
            or_(
                DocumentChunk.knowledge_document_id == record.id,
                and_(*legacy_identity),
            ),
        )
    else:
        chunk_query = db.query(DocumentChunk).filter(*legacy_identity)

    chunks = chunk_query.all()
    if not chunks and not record:
        raise HTTPException(status_code=404, detail="Document not found")

    if current_user.role == "Manager":
        departments = {chunk.department_access for chunk in chunks}
        if record:
            departments.add(record.department)
        if any(department not in {"ALL", current_user.department} for department in departments):
            raise HTTPException(status_code=403, detail="Manager cannot delete this document")

    storage_key = record.storage_key if record else None
    for chunk in chunks:
        db.delete(chunk)
    if record:
        db.delete(record)
    db.flush()
    # In the same transaction as the delete: an agent scoped to this document must not
    # keep a selector the configuration page cannot show and the update API rejects.
    pruned_agents = prune_orphaned_knowledge_selectors(db, current_user.tenant_id)
    db.commit()

    file_deleted = False
    if storage_key:
        try:
            file_deleted = delete_original_file(storage_key)
        except (OSError, ValueError):
            logger.warning(
                "Document metadata was deleted but original-file cleanup failed for %s",
                storage_key,
                exc_info=True,
            )
    return {
        "message": "Document deleted successfully",
        "document_id": document_id,
        "version": version,
        "chunks_deleted": len(chunks),
        "file_deleted": file_deleted,
        "agents_rescoped": sorted(agent.role_code for agent in pruned_agents),
    }
