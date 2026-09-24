"""Small Redis-backed at-least-once queue used by background workers."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def enqueue_job(job_type: str, payload: dict[str, Any], dedup_key: str) -> bool:
    client = _client()
    lock_key = f"{settings.WORK_QUEUE_NAME}:dedup:{dedup_key}"
    if not client.set(lock_key, "1", nx=True, ex=86400):
        return False
    try:
        client.lpush(
            settings.WORK_QUEUE_NAME,
            json.dumps(
                {"type": job_type, "payload": payload, "dedup_key": dedup_key},
                separators=(",", ":"),
            ),
        )
        return True
    except Exception:
        client.delete(lock_key)
        raise


def acknowledge_job(raw_job: str, dedup_key: str) -> None:
    client = _client()
    client.lrem(settings.WORK_QUEUE_PROCESSING_NAME, 1, raw_job)
    client.delete(f"{settings.WORK_QUEUE_NAME}:dedup:{dedup_key}")


def requeue_job(raw_job: str, dedup_key: str) -> None:
    client = _client()
    pipe = client.pipeline()
    pipe.lrem(settings.WORK_QUEUE_PROCESSING_NAME, 1, raw_job)
    pipe.rpush(settings.WORK_QUEUE_NAME, raw_job)
    pipe.expire(f"{settings.WORK_QUEUE_NAME}:dedup:{dedup_key}", 86400)
    pipe.execute()


def dead_letter_job(raw_job: str, reason: str, dedup_key: str | None = None) -> None:
    """Remove a poison message from processing and preserve it for inspection."""
    client = _client()
    envelope = json.dumps(
        {
            "raw_job": raw_job,
            "reason": reason[:2000],
            "failed_at": datetime.now(timezone.utc).isoformat(),
        },
        separators=(",", ":"),
    )
    pipe = client.pipeline()
    pipe.lrem(settings.WORK_QUEUE_PROCESSING_NAME, 1, raw_job)
    pipe.lpush(settings.WORK_QUEUE_DEAD_LETTER_NAME, envelope)
    if dedup_key:
        pipe.delete(f"{settings.WORK_QUEUE_NAME}:dedup:{dedup_key}")
    pipe.execute()


def recover_interrupted_jobs() -> int:
    client = _client()
    recovered = 0
    while True:
        raw_job = client.rpoplpush(
            settings.WORK_QUEUE_PROCESSING_NAME, settings.WORK_QUEUE_NAME
        )
        if raw_job is None:
            return recovered
        recovered += 1


def reserve_job(timeout: int = 5) -> str | None:
    try:
        return _client().brpoplpush(
            settings.WORK_QUEUE_NAME,
            settings.WORK_QUEUE_PROCESSING_NAME,
            timeout=timeout,
        )
    except redis.exceptions.TimeoutError:
        # redis-py 8 can surface an empty blocking-pop timeout as an exception
        # instead of returning None. An idle queue is a normal worker state.
        return None


def heartbeat_worker() -> None:
    _client().set(
        settings.WORKER_HEARTBEAT_KEY,
        datetime.now(timezone.utc).isoformat(),
        ex=20,
    )


def queue_stats() -> dict[str, Any]:
    """Return non-sensitive operational queue health."""
    client = _client()
    values = client.pipeline()
    values.llen(settings.WORK_QUEUE_NAME)
    values.llen(settings.WORK_QUEUE_PROCESSING_NAME)
    values.llen(settings.WORK_QUEUE_DEAD_LETTER_NAME)
    values.get(settings.WORKER_HEARTBEAT_KEY)
    queued, processing, dead_letter, heartbeat = values.execute()
    return {
        "available": True,
        "queued": queued,
        "processing": processing,
        "dead_letter": dead_letter,
        "worker_online": heartbeat is not None,
        "worker_last_seen": heartbeat,
    }
