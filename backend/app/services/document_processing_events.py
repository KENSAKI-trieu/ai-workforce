"""In-process delivery of ordered knowledge-ingestion progress snapshots."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any

from app.models.models import KnowledgeDocument

_MAX_DOCUMENTS = 256
_MAX_EVENTS_PER_DOCUMENT = 4096
_condition = threading.Condition()
_next_sequence = 0
_events: OrderedDict[
    uuid.UUID,
    deque[tuple[int, dict[str, Any]]],
] = OrderedDict()
# Active SSE listeners per document. Ingestion waits on this so the browser is
# attached before the first stage runs, which keeps every transition live
# instead of arriving as a backlog the client replays at frame rate.
_subscribers: dict[uuid.UUID, int] = {}

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
                # A small embedding batch size emits one snapshot per chunk, so
                # a large document can produce thousands of frames that differ
                # only in their counters. Collapsing consecutive frames of the
                # same stage and percentage keeps full detail without letting
                # the bounded history evict the earlier pipeline stages.
                if (
                    previous.get("processing_status") == payload.get("processing_status")
                    and previous.get("processing_progress")
                    == payload.get("processing_progress")
                ):
                    history.pop()
        _next_sequence += 1
        history.append((_next_sequence, payload))
        _condition.notify_all()


def reset_processing_events(record_id: uuid.UUID) -> None:
    """Discard terminal history before a new attempt reuses the same record."""
    with _condition:
        _events.pop(record_id, None)


def mark_subscriber(record_id: uuid.UUID) -> None:
    """Register an attached SSE listener and release any waiting ingestion."""
    with _condition:
        _subscribers[record_id] = _subscribers.get(record_id, 0) + 1
        _condition.notify_all()


def clear_subscriber(record_id: uuid.UUID) -> None:
    """Drop one attached SSE listener once its stream closes."""
    with _condition:
        remaining = _subscribers.get(record_id, 0) - 1
        if remaining > 0:
            _subscribers[record_id] = remaining
        else:
            _subscribers.pop(record_id, None)


def wait_for_subscriber(record_id: uuid.UUID, *, timeout: float) -> bool:
    """Block until a listener attaches, returning False when the wait expires.

    The wait is bounded on purpose: an upload driven by a script or an API
    client never attaches a stream, and ingestion must still run for it.
    """
    deadline = time.monotonic() + timeout
    with _condition:
        while not _subscribers.get(record_id):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _condition.wait(timeout=remaining)
        return True


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
