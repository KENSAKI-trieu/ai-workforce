"""Checkpointed, resumable ingestion for uploaded knowledge documents."""

from __future__ import annotations

import logging
import threading
import uuid

from sqlalchemy.orm import Session

from app.models.models import DocumentChunk, KnowledgeDocument, User
from app.services.document_parser import DocumentParseError, extract_file_text
from app.services.embedding_service import (
    build_embedding_text,
    calculate_content_hash,
    get_embedding_service,
)
from app.services.knowledge_storage import read_original_file
from app.services.notification_service import create_notification
from app.services.rag_service import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    build_configured_chunks,
    chunk_document_content,
)

logger = logging.getLogger(__name__)

_locks_guard = threading.Lock()
_document_locks: dict[uuid.UUID, threading.Lock] = {}

_FAILED_STAGE_BY_CHECKPOINT = {
    "uploaded": "parsing",
    "parsed": "chunking",
    "chunked": "embedding",
    "embedded": "indexing",
    "ready": "ready",
}


class DocumentAlreadyProcessing(RuntimeError):
    """Raised when another request is already advancing this document."""


def failed_stage_from_checkpoint(checkpoint: str | None) -> str:
    """Identify the pipeline stage immediately following a durable checkpoint."""
    return _FAILED_STAGE_BY_CHECKPOINT.get(checkpoint or "uploaded", "parsing")


def _document_lock(record_id: uuid.UUID) -> threading.Lock:
    with _locks_guard:
        return _document_locks.setdefault(record_id, threading.Lock())


def _checkpoint_chunks(db: Session, record: KnowledgeDocument) -> list[DocumentChunk]:
    if not record.parsed_text:
        raise RuntimeError("Parsed document text is not available")

    embedding_service = get_embedding_service()
    chunking_config = record.chunking_config or {}
    mode = str(chunking_config.get("mode", "standard"))
    chunk_size = int(chunking_config.get("chunk_size", CHUNK_SIZE_TOKENS))
    chunk_overlap = int(chunking_config.get("chunk_overlap", CHUNK_OVERLAP_TOKENS))
    parent_chunk_size = int(chunking_config.get("parent_chunk_size", 1024))
    if mode == "standard" and chunk_size == CHUNK_SIZE_TOKENS and chunk_overlap == CHUNK_OVERLAP_TOKENS:
        raw_chunks = chunk_document_content(record.parsed_text)
    else:
        raw_chunks = build_configured_chunks(
            record.parsed_text,
            mode=mode,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            parent_chunk_size=parent_chunk_size,
        )
    prepared: list[dict] = []
    seen_hashes: set[str] = set()
    for chunk_data in raw_chunks:
        content_hash = calculate_content_hash(chunk_data["content"])
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        embedding_text = build_embedding_text({
            "department": record.department,
            "document_type": record.document_type,
            "document_title": record.document_title,
            "section_title": chunk_data["section_title"],
            "content": chunk_data["content"],
        })
        prepared.append({
            **chunk_data,
            "content_hash": content_hash,
            "embedding_text": embedding_text,
        })

    token_counts = embedding_service.count_tokens_batch([
        item["embedding_text"] for item in prepared
    ])
    for chunk_data, token_count in zip(prepared, token_counts):
        if token_count > embedding_service.max_input_tokens:
            raise ValueError(
                f"Embedding input exceeds model limit: {token_count} > "
                f"{embedding_service.max_input_tokens}"
            )
        chunk_data["embedding_token_count"] = token_count

    db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == record.tenant_id,
        DocumentChunk.document_id == record.document_id,
        DocumentChunk.version == record.version,
    ).delete(synchronize_session=False)
    db.flush()

    chunks: list[DocumentChunk] = []
    for index, chunk_data in enumerate(prepared):
        page_start = chunk_data["page"]
        page_end = max(chunk_data["pages"]) if chunk_data["pages"] else page_start
        chunk = DocumentChunk(
            id=uuid.uuid4(),
            tenant_id=record.tenant_id,
            knowledge_document_id=record.id,
            document_name=record.file_name,
            document_id=record.document_id,
            document_title=record.document_title,
            document_type=record.document_type,
            version=record.version,
            effective_date=record.effective_date,
            expiration_date=record.expiration_date,
            # Checkpoint chunks must not participate in RAG before indexing.
            status="draft",
            confidentiality=record.confidentiality,
            allowed_roles=record.allowed_roles or [],
            source_file=record.file_name,
            collection_name=record.collection_name,
            department_access=record.department,
            chunk_index=index,
            section_title=chunk_data["section_title"],
            page=page_start,
            page_start=page_start,
            page_end=page_end,
            content=chunk_data["content"],
            embedding_text=chunk_data["embedding_text"],
            content_hash=chunk_data["content_hash"],
            embedding_model=embedding_service.model_name,
            embedding_version=embedding_service.version,
            embedding_status="pending",
            embedding=None,
            metadata_={
                "document_id": record.document_id,
                "document_title": record.document_title,
                "document_type": record.document_type,
                "version": record.version,
                "effective_date": (
                    record.effective_date.isoformat() if record.effective_date else None
                ),
                "expiration_date": (
                    record.expiration_date.isoformat() if record.expiration_date else None
                ),
                "status": record.status,
                "confidentiality": record.confidentiality,
                "allowed_roles": record.allowed_roles or [],
                "source_file": record.file_name,
                "section_title": chunk_data["section_title"],
                "section_type": chunk_data["section_type"],
                "document_name": record.file_name,
                "section_index": chunk_data["section_index"],
                "section_chunk_index": chunk_data["section_chunk_index"],
                "header_level": chunk_data["header_level"],
                "header_path": chunk_data["header_path"],
                "page_start": page_start,
                "page_end": page_end,
                "pages": chunk_data["pages"],
                "token_count": chunk_data["token_count"],
                "chunking_mode": chunk_data.get("chunking_mode", "standard"),
                "parent_chunk_index": chunk_data.get("parent_chunk_index"),
                "parent_content": chunk_data.get("parent_content"),
                "child_chunk_index": chunk_data.get("child_chunk_index"),
                "embedding_token_count": chunk_data["embedding_token_count"],
                "content_hash": chunk_data["content_hash"],
                "embedding_model": embedding_service.model_name,
                "embedding_version": embedding_service.version,
            },
        )
        db.add(chunk)
        chunks.append(chunk)

    record.embedding_model = embedding_service.model_name
    record.embedding_version = embedding_service.version
    record.chunk_count = len(chunks)
    record.processing_checkpoint = "chunked"
    record.processing_status = "embedding"
    record.processing_progress = 0
    db.commit()
    return chunks


def _embed_checkpointed_chunks(
    db: Session, record: KnowledgeDocument
) -> list[DocumentChunk]:
    embedding_service = get_embedding_service()
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.knowledge_document_id == record.id,
    ).order_by(DocumentChunk.chunk_index).all()
    if not chunks:
        raise RuntimeError("Chunk checkpoint is not available")

    # An interrupted embedding stage always restarts from the saved chunk
    # checkpoint, as opposed to trusting a partially completed vector batch.
    for chunk in chunks:
        chunk.embedding = None
        chunk.embedding_status = "pending"
        chunk.embedding_model = embedding_service.model_name
        chunk.embedding_version = embedding_service.version
        if chunk.metadata_ is not None:
            chunk.metadata_ = {
                **chunk.metadata_,
                "embedding_model": embedding_service.model_name,
                "embedding_version": embedding_service.version,
            }
    record.embedding_model = embedding_service.model_name
    record.embedding_version = embedding_service.version
    record.processing_status = "embedding"
    record.processing_progress = 0
    db.commit()

    total = len(chunks)
    for batch_start in range(0, total, embedding_service.batch_size):
        batch = chunks[batch_start:batch_start + embedding_service.batch_size]
        vectors = embedding_service.embed_texts([
            chunk.embedding_text or chunk.content for chunk in batch
        ])
        if len(vectors) != len(batch):
            raise RuntimeError("Embedding provider returned an unexpected vector count")
        for chunk, vector in zip(batch, vectors):
            chunk.embedding = vector
            chunk.embedding_status = "embedded"
        completed = min(batch_start + len(batch), total)
        record.processing_progress = round((completed / total) * 100)
        db.commit()

    record.processing_checkpoint = "embedded"
    record.processing_status = "indexing"
    record.processing_progress = 0
    db.commit()
    return chunks


def _index_checkpointed_chunks(
    db: Session, record: KnowledgeDocument
) -> list[DocumentChunk]:
    record = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.id == record.id
    ).populate_existing().with_for_update().one()
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.knowledge_document_id == record.id,
    ).order_by(DocumentChunk.chunk_index).all()
    if not chunks or any(chunk.embedding is None for chunk in chunks):
        raise RuntimeError("Embedding checkpoint is incomplete")

    if record.status == "active":
        db.query(KnowledgeDocument).filter(
            KnowledgeDocument.tenant_id == record.tenant_id,
            KnowledgeDocument.document_id == record.document_id,
            KnowledgeDocument.version != record.version,
            KnowledgeDocument.status == "active",
        ).update({KnowledgeDocument.status: "inactive"}, synchronize_session=False)
        db.query(DocumentChunk).filter(
            DocumentChunk.tenant_id == record.tenant_id,
            DocumentChunk.document_id == record.document_id,
            DocumentChunk.version != record.version,
            DocumentChunk.status == "active",
        ).update({DocumentChunk.status: "inactive"}, synchronize_session=False)

    for chunk in chunks:
        chunk.status = record.status
        chunk.embedding_status = "embedded"
    record.processing_checkpoint = "ready"
    record.processing_status = "ready"
    record.processing_progress = 100
    record.error_message = None
    record.parsed_text = None
    db.commit()
    return chunks


def resume_document_ingestion(db: Session, record_id: uuid.UUID) -> list[DocumentChunk]:
    """Advance a document from its last durable checkpoint to `ready`."""
    lock = _document_lock(record_id)
    if not lock.acquire(blocking=False):
        raise DocumentAlreadyProcessing("Document processing is already running")
    checkpoint = "uploaded"
    try:
        record = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id == record_id
        ).first()
        if not record:
            raise LookupError("Document not found")
        if record.processing_checkpoint == "ready" or record.processing_status == "ready":
            return db.query(DocumentChunk).filter(
                DocumentChunk.knowledge_document_id == record.id
            ).order_by(DocumentChunk.chunk_index).all()
        if not record.storage_key:
            raise RuntimeError("Original document is not available")

        record.processing_attempts += 1
        record.error_message = None
        db.commit()

        checkpoint = record.processing_checkpoint or "uploaded"
        if checkpoint == "uploaded":
            record.processing_status = "parsing"
            record.processing_progress = 0
            db.commit()
            original = read_original_file(record.storage_key)
            parsed_text = extract_file_text(record.file_name, original).strip()
            if not parsed_text:
                raise DocumentParseError("No readable text found in the file")
            record.parsed_text = parsed_text
            record.processing_checkpoint = "parsed"
            record.processing_status = "chunking"
            record.processing_progress = 0
            db.commit()
            checkpoint = "parsed"

        if checkpoint == "parsed":
            _checkpoint_chunks(db, record)
            checkpoint = "chunked"

        if checkpoint == "chunked":
            _embed_checkpointed_chunks(db, record)
            checkpoint = "embedded"

        if checkpoint == "embedded":
            chunks = _index_checkpointed_chunks(db, record)
        else:
            chunks = db.query(DocumentChunk).filter(
                DocumentChunk.knowledge_document_id == record.id
            ).order_by(DocumentChunk.chunk_index).all()

        creator = (
            db.query(User).filter(User.id == record.created_by_id).first()
            if record.created_by_id
            else None
        )
        if creator:
            create_notification(
                db,
                user=creator,
                event_type="DOCUMENT_READY",
                title="Tài liệu đã xử lý xong",
                message=f"{record.file_name}: {len(chunks)} chunks đã được lập chỉ mục.",
                severity="SUCCESS",
                entity_type="DOCUMENT",
                entity_id=record.document_id,
                dedup_key=f"document-ready:{record.id}:{record.source_hash}",
            )
            db.commit()
        return chunks
    except DocumentAlreadyProcessing:
        raise
    except Exception as exc:
        failed_stage = failed_stage_from_checkpoint(checkpoint)
        logger.exception(
            "Checkpointed ingestion failed for document %s at stage %s",
            record_id,
            failed_stage,
        )
        db.rollback()
        failed = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id == record_id
        ).first()
        if failed:
            failed.processing_status = "failed"
            failed.error_message = str(exc)[:2000]
            db.commit()
        raise
    finally:
        lock.release()
