"""Durability tests for checkpointed knowledge-document ingestion."""

import contextlib
import json
import threading
import uuid

import pytest

from app.api.v1 import documents
from app.models.models import DocumentChunk, KnowledgeDocument
from app.services import document_ingestion
from app.services.document_processing_events import clear_subscriber, mark_subscriber
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
    scheduled: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        documents,
        "_resume_document_ingestion_background",
        lambda record_id, progress_stream_id: scheduled.append(
            (record_id, progress_stream_id)
        ),
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
    assert response.json()["processing_stream_id"]
    assert scheduled == [(record.id, response.json()["processing_stream_id"])]
    assert record.processing_status == "uploaded"
    assert record.processing_checkpoint == "uploaded"


def test_async_replace_skips_ai_duplicate_chunk_preflight(
    client, ceo_token_headers, monkeypatch
):
    document_id = f"async-replace-{uuid.uuid4().hex}"
    first = _upload(client, ceo_token_headers, document_id)
    assert first.status_code == 201

    def unexpected_duplicate_preflight(*args, **kwargs):
        raise AssertionError("async upload must not chunk before returning 202")

    scheduled: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        documents,
        "_duplicate_chunk_report",
        unexpected_duplicate_preflight,
    )
    monkeypatch.setattr(
        documents,
        "_resume_document_ingestion_background",
        lambda record_id, progress_stream_id: scheduled.append(
            (record_id, progress_stream_id)
        ),
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
                b"# Durable ingestion\nCheckpoint this document before embedding.",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 202
    assert response.json()["processing_stream_id"]
    assert scheduled


def test_async_exact_duplicate_prompt_uses_source_hash_without_ai_preflight(
    client, ceo_token_headers, monkeypatch
):
    document_id = f"async-duplicate-{uuid.uuid4().hex}"
    first = _upload(client, ceo_token_headers, document_id)
    assert first.status_code == 201
    monkeypatch.setattr(
        documents,
        "_duplicate_chunk_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("exact async duplicate must use source hash")
        ),
    )

    response = client.post(
        "/api/v1/documents/upload",
        headers=ceo_token_headers,
        data={
            "document_id": document_id,
            "version": "1.0",
            "duplicate_strategy": "prompt",
            "async_processing": "true",
        },
        files={
            "file": (
                f"{document_id}.md",
                b"# Durable ingestion\nCheckpoint this document before embedding.",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DUPLICATE_CHUNKS"


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
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    statuses = [payload["processing_status"] for payload in payloads]
    assert statuses.index("parsing") < statuses.index("chunking")
    assert statuses.index("chunking") < statuses.index("embedding")
    assert statuses.index("embedding") < statuses.index("indexing")
    assert statuses.index("indexing") < statuses.index("ready")
    assert any(
        payload["processing_status"] == "chunking"
        and payload.get("chunk_segments_processed") == 1
        and payload.get("chunk_segments_remaining") == 0
        and payload.get("chunks_created") == 1
        for payload in payloads
    )
    assert any(
        payload["processing_status"] == "embedding"
        and payload.get("embedded_chunks") == 1
        and payload.get("embedding_remaining_chunks") == 0
        for payload in payloads
    )


def test_completed_history_streams_as_replay_instead_of_live_status(
    client, ceo_token_headers, transactional_db_session, monkeypatch
):
    """A late subscriber must be able to tell history from live activity."""
    document_id = f"checkpoint-replay-{uuid.uuid4().hex}"
    assert _upload(client, ceo_token_headers, document_id).status_code == 201

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
    # Ingestion already finished, so every intermediate stage is backlog.
    assert "event: replay" in response.text
    assert "event: status" not in response.text
    assert "event: ready" in response.text


def test_async_ingestion_waits_for_the_progress_subscriber(monkeypatch):
    """Stages must not run before the uploader's stream is attached."""
    record_id = uuid.uuid4()
    started = threading.Event()
    monkeypatch.setattr(
        documents,
        "SyncSessionLocal",
        lambda: contextlib.nullcontext(object()),
    )
    monkeypatch.setattr(
        documents,
        "resume_document_ingestion",
        lambda *_args, **_kwargs: started.set(),
    )

    worker = threading.Thread(
        target=documents._resume_document_ingestion_background,
        args=(record_id, "stream-id"),
    )
    worker.start()
    try:
        assert not started.wait(timeout=0.3), "ingestion ran before a subscriber attached"
        mark_subscriber(record_id)
        assert started.wait(timeout=2.0), "ingestion did not resume once attached"
    finally:
        worker.join(timeout=5)
        clear_subscriber(record_id)


def test_async_ingestion_runs_when_no_subscriber_ever_attaches(monkeypatch):
    """Script and API uploads never open a stream and must not stall."""
    record_id = uuid.uuid4()
    started = threading.Event()
    monkeypatch.setattr(documents, "SUBSCRIBER_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(
        documents,
        "SyncSessionLocal",
        lambda: contextlib.nullcontext(object()),
    )
    monkeypatch.setattr(
        documents,
        "resume_document_ingestion",
        lambda *_args, **_kwargs: started.set(),
    )

    documents._resume_document_ingestion_background(record_id, "stream-id")

    assert started.is_set()


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

        def embed_texts(self, _texts, **_kwargs):
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

    def fail_chunking(_content, **_kwargs):
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
