"""Background worker entrypoint: python -m app.worker."""

import json
import logging
import time
import uuid

from app.core.database import SyncSessionLocal
from app.domains.executive.scheduler import run_due as run_executive_schedule
from app.domains.support.support_workflow import execute_support_case
from app.domains.platform.work_queue import (
    acknowledge_job,
    dead_letter_job,
    heartbeat_worker,
    recover_interrupted_jobs,
    requeue_job,
    reserve_job,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_workforce.worker")
# How often the timed work runs: the CEO assistant's alerts and morning digest.
SCHEDULE_EVERY_SECONDS = 60


def run_schedule() -> None:
    try:
        with SyncSessionLocal() as db:
            done = run_executive_schedule(db)
        if any(done.values()):
            logger.info("Executive schedule sent %s alerts and %s digests", done["alerts"], done["digests"])
    except Exception:
        logger.exception("Executive schedule pass failed")


def run() -> None:
    recovered = recover_interrupted_jobs()
    logger.info("Worker started; recovered %s interrupted jobs", recovered)
    next_schedule = 0.0
    while True:
        heartbeat_worker()
        if time.monotonic() >= next_schedule:
            run_schedule()
            next_schedule = time.monotonic() + SCHEDULE_EVERY_SECONDS
        raw_job = reserve_job(timeout=5)
        if raw_job is None:
            continue
        job: dict = {}
        try:
            job = json.loads(raw_job)
            if job["type"] != "support.execute":
                raise ValueError(f"Unsupported job type: {job['type']}")
            with SyncSessionLocal() as db:
                result = execute_support_case(
                    db, uuid.UUID(job["payload"]["support_case_id"])
                )
            if result == "RETRY":
                time.sleep(2)
                requeue_job(raw_job, job["dedup_key"])
            else:
                acknowledge_job(raw_job, job["dedup_key"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            logger.exception("Invalid or unsupported worker job")
            try:
                dead_letter_job(raw_job, str(error), job.get("dedup_key"))
            except Exception:
                logger.exception("Failed to dead-letter poison job")
            time.sleep(1)
        except Exception:
            logger.exception("Worker job failed unexpectedly")
            try:
                requeue_job(raw_job, job.get("dedup_key", "unknown"))
            except Exception:
                logger.exception("Failed to requeue job")
            time.sleep(2)


if __name__ == "__main__":
    run()
