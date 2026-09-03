"""In-process delivery of ordered knowledge-ingestion progress snapshots."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict, deque
from typing import Any

from app.models.models import KnowledgeDocument

_MAX_DOCUMENTS = 256
_MAX_EVENTS_PER_DOCUMENT = 512
_condition = threading.Condition()
_next_sequence = 0
_events: OrderedDict[
    uuid.UUID,
    deque[tuple[int, dict[str, Any]]],
] = OrderedDict()

_FAILED_STAGE_BY_CHECKPOINT = {
    "uploaded": "parsing",
    "parsed": "chunking",
    "chunked": "embedding",
    "embedded": "indexing",
    "ready": "ready",
}


def processing_status_payload(
    record: KnowledgeDocument,
    **progress_details: int,
) -> dict[str, Any]:
    """Create the public progress contract from a committed document record."""
    failed_stage = (
        _FAILED_STAGE_BY_CHECKPOINT.get(
            record.processing_checkpoint or "uploaded",
            "parsing",
        )
        if record.processing_status == "failed"
        else None
    )
    payload = {
        "document_id": record.document_id,
        "document_name": record.file_name,
        "version": record.version,
        "processing_status": record.processing_status,
        "processing_checkpoint": record.processing_checkpoint,
        "processing_progress": record.processing_progress,
        "processing_attempts": record.processing_attempts,
        "chunk_count": record.chunk_count,
        "embedding_model": record.embedding_model,
        "failed_stage": failed_stage,
        "error_message": record.error_message,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }
    payload.update(progress_details)
    return payload


def publish_processing_status(
    record: KnowledgeDocument,
    **progress_details: int,
) -> None:
    """Store and wake SSE listeners after a processing progress commit."""
    global _next_sequence
    payload = processing_status_payload(record, **progress_details)
    with _condition:
        history = _events.get(record.id)
        if history is None:
            if len(_events) >= _MAX_DOCUMENTS:
                _events.popitem(last=False)
            history = deque(maxlen=_MAX_EVENTS_PER_DOCUMENT)
            _events[record.id] = history
        else:
            _events.move_to_end(record.id)
            if history:
                previous = history[-1][1]
                comparable_keys = payload.keys() - {"updated_at"}
                if all(previous.get(key) == payload.get(key) for key in comparable_keys):
                    return
        _next_sequence += 1
        history.append((_next_sequence, payload))
        _condition.notify_all()


def reset_processing_events(record_id: uuid.UUID) -> None:
    """Discard terminal history before a new attempt reuses the same record."""
    with _condition:
        _events.pop(record_id, None)


def processing_events_after(
    record_id: uuid.UUID,
    sequence: int,
) -> list[tuple[int, dict[str, Any]]]:
    """Return ordered snapshots newer than ``sequence`` for one document."""
    with _condition:
        history = _events.get(record_id, ())
        return [event for event in history if event[0] > sequence]


def wait_for_processing_event(
    record_id: uuid.UUID,
    sequence: int,
    *,
    timeout: float,
) -> None:
    """Block briefly until a newer event is published or the timeout elapses."""
    with _condition:
        history = _events.get(record_id, ())
        if any(event_sequence > sequence for event_sequence, _ in history):
            return
        _condition.wait(timeout=timeout)
