"""Durability tests for checkpointed knowledge-document ingestion."""

import uuid

import pytest

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
