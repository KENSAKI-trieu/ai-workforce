"""Durability tests for checkpointed knowledge-document ingestion."""

import uuid

import pytest

from app.api.v1 import documents
from app.models.models import DocumentChunk, KnowledgeDocument
from app.services import document_ingestion
from app.services.embedding_service import get_embedding_service


def _upload(client, headers, document_id: str):
    return client.post(
        "/api/v1/documents/upload",
        headers=headers,
        data={
            "document_id": document_id,
            "version": "1.0",
            "duplicate_strategy": "replace",
        },
        files={
            "file": (
                f"{document_id}.md",
                b"# Durable ingestion\nCheckpoint this document before embedding.",
                "text/markdown",
            )
        },
    )


def test_upload_runs_without_document_worker(
    client, ceo_token_headers, transactional_db_session
):
    document_id = f"checkpoint-sync-{uuid.uuid4().hex}"
    response = _upload(client, ceo_token_headers, document_id)

    assert response.status_code == 201
    assert response.json()["status"] == "INDEXED"
    record = transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == document_id,
    ).one()
    assert record.processing_status == "ready"
    assert record.processing_checkpoint == "ready"
    assert record.chunk_count >= 1


def test_async_upload_acknowledges_before_ingestion(
    client, ceo_token_headers, transactional_db_session, monkeypatch
):
    document_id = f"checkpoint-async-{uuid.uuid4().hex}"
    scheduled: list[uuid.UUID] = []
    monkeypatch.setattr(
        documents,
        "_resume_document_ingestion_background",
        lambda record_id: scheduled.append(record_id),
    )

    response = client.post(
        "/api/v1/documents/upload",
        headers=ceo_token_headers,
        data={
            "document_id": document_id,
            "version": "1.0",
            "duplicate_strategy": "replace",
            "async_processing": "true",
        },
        files={
            "file": (
                f"{document_id}.md",
                b"# Async ingestion\nAcknowledge the upload before embedding.",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "ACCEPTED"
    assert response.json()["processing_status"] == "uploaded"
    record = transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == document_id,
    ).one()
    assert scheduled == [record.id]
    assert record.processing_status == "uploaded"
    assert record.processing_checkpoint == "uploaded"


def test_processing_event_stream_emits_terminal_status(
    client, ceo_token_headers, transactional_db_session, monkeypatch
):
    document_id = f"checkpoint-events-{uuid.uuid4().hex}"
    uploaded = _upload(client, ceo_token_headers, document_id)
    assert uploaded.status_code == 201

    class SharedSession:
        def __enter__(self):
            return transactional_db_session

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(documents, "SyncSessionLocal", SharedSession)
    response = client.get(
        f"/api/v1/documents/processing-events/{document_id}",
        params={"version": "1.0"},
        headers=ceo_token_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: ready" in response.text
    assert '"processing_status": "ready"' in response.text


def test_chunk_checkpoint_accepts_section_titles_longer_than_500_characters(
    client, ceo_token_headers, transactional_db_session
):
    document_id = f"long-section-title-{uuid.uuid4().hex}"
    long_title = " ".join(f"heading-{index}" for index in range(80))
    response = client.post(
        "/api/v1/documents/upload",
        headers=ceo_token_headers,
        data={
            "document_id": document_id,
            "version": "1.0",
            "duplicate_strategy": "replace",
        },
        files={
            "file": (
                f"{document_id}.md",
                f"# {long_title}\nLong-title regression content.".encode(),
                "text/markdown",
            )
        },
    )

    assert response.status_code == 201
    chunk = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id,
    ).one()
    assert chunk.section_title == long_title
    assert len(chunk.section_title) > 500


def test_interrupted_embedding_is_visible_and_resumes_from_saved_chunks(
    client, ceo_token_headers, transactional_db_session, monkeypatch
):
    document_id = f"checkpoint-embedding-{uuid.uuid4().hex}"
    real_service = get_embedding_service()

    class FailingEmbeddingService:
        def __getattr__(self, name):
            return getattr(real_service, name)

        def embed_texts(self, _texts):
            raise RuntimeError("temporary embedding outage")

    monkeypatch.setattr(
        document_ingestion,
        "get_embedding_service",
        lambda: FailingEmbeddingService(),
    )
    with pytest.raises(RuntimeError, match="temporary embedding outage"):
        _upload(client, ceo_token_headers, document_id)

    record = transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == document_id,
    ).one()
    assert record.processing_status == "failed"
    assert record.processing_checkpoint == "chunked"
    chunks = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.knowledge_document_id == record.id,
    ).order_by(DocumentChunk.chunk_index).all()
    saved_chunk_ids = [chunk.id for chunk in chunks]
    assert saved_chunk_ids
    assert all(chunk.status == "draft" for chunk in chunks)

    status = client.get(
        f"/api/v1/documents/processing-status/{document_id}",
        params={"version": "1.0"},
        headers=ceo_token_headers,
    )
    assert status.status_code == 200
    assert status.json()["processing_status"] == "failed"
    assert status.json()["failed_stage"] == "embedding"
    assert "temporary embedding outage" in status.json()["error_message"]

    # The Knowledge page must fetch the durable document record even while its
    # checkpoint chunks are still draft and therefore excluded from RAG search.
    listed = client.get("/api/v1/documents", headers=ceo_token_headers)
    assert listed.status_code == 200
    item = next(item for item in listed.json() if item["document_id"] == document_id)
    assert item["processing_checkpoint"] == "chunked"
    assert item["chunk_count"] == len(saved_chunk_ids)

    class RecoveryEmbeddingService:
        model_name = "recovery-embedding-model"
        version = "recovery-v1"

        def __getattr__(self, name):
            return getattr(real_service, name)

    recovery_service = RecoveryEmbeddingService()
    monkeypatch.setattr(
        document_ingestion,
        "get_embedding_service",
        lambda: recovery_service,
    )
    resumed = client.post(
        f"/api/v1/documents/{document_id}/retry",
        params={"version": "1.0"},
        headers=ceo_token_headers,
    )

    assert resumed.status_code == 200
    assert resumed.json()["processing_status"] == "ready"
    transactional_db_session.expire_all()
    resumed_chunks = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.knowledge_document_id == record.id,
    ).order_by(DocumentChunk.chunk_index).all()
    assert [chunk.id for chunk in resumed_chunks] == saved_chunk_ids
    assert all(chunk.embedding is not None for chunk in resumed_chunks)
    assert all(chunk.status == "active" for chunk in resumed_chunks)
    assert all(chunk.embedding_model == recovery_service.model_name for chunk in resumed_chunks)
    assert all(chunk.embedding_version == recovery_service.version for chunk in resumed_chunks)


def test_interrupted_chunking_resumes_without_parsing_again(
    client, ceo_token_headers, transactional_db_session, monkeypatch
):
    document_id = f"checkpoint-chunking-{uuid.uuid4().hex}"
    real_chunker = document_ingestion.chunk_document_content

    def fail_chunking(_content):
        raise RuntimeError("chunker interrupted")

    monkeypatch.setattr(document_ingestion, "chunk_document_content", fail_chunking)
    with pytest.raises(RuntimeError, match="chunker interrupted"):
        _upload(client, ceo_token_headers, document_id)

    record = transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == document_id,
    ).one()
    assert record.processing_checkpoint == "parsed"
    assert record.parsed_text

    monkeypatch.setattr(document_ingestion, "chunk_document_content", real_chunker)

    def parsing_must_not_run(_filename, _data):
        raise AssertionError("parser ran after the parsed checkpoint")

    monkeypatch.setattr(document_ingestion, "extract_file_text", parsing_must_not_run)
    resumed = client.post(
        f"/api/v1/documents/{document_id}/retry",
        params={"version": "1.0"},
        headers=ceo_token_headers,
    )

    assert resumed.status_code == 200
    transactional_db_session.refresh(record)
    assert record.processing_checkpoint == "ready"
    assert record.processing_status == "ready"
