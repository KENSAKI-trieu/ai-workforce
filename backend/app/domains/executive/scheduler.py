"""Sending the CEO assistant's alerts and morning digest on time, from the worker.

The worker calls `run_due` about once a minute. For each tenant it locks that tenant's
schedule row (SKIP LOCKED, so a second worker moves on), checks the alert rules every
ALERT_INTERVAL and sends the digest once on each chosen weekday, at the chosen hour of
the tenant's own day or the first run after it. Recipients are the active people whose
position may use the CEO assistant; each is evaluated with their own permissions.

Notifications are deduplicated per person, rule, subject and day, so a check that runs
again -- a restart, a second worker -- never repeats one.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.executive.alerts import evaluate_alerts, rule_settings
from app.domains.executive.common import local_today, tenant_zone
from app.domains.executive.digest import build_digest
from app.domains.platform.notification_service import create_notification
from app.domains.platform.position_service import user_permissions
from app.models.models import ExecutiveSchedule, Tenant, User

logger = logging.getLogger(__name__)

USE_PERMISSION = "executive.briefing.view"
ALERT_INTERVAL = timedelta(hours=2)
WEEKDAYS = range(1, 8)


def recipients(db: Session, tenant_id: uuid.UUID) -> list[User]:
    people = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True)).all()
    return [person for person in people if USE_PERMISSION in user_permissions(db, person)]


def get_schedule(db: Session, tenant_id: uuid.UUID, *, lock: bool = False) -> ExecutiveSchedule | None:
    """The tenant's schedule row, created with the defaults on first use.

    With ``lock`` it is taken FOR UPDATE SKIP LOCKED, and None means another worker holds it.
    """
    query = db.query(ExecutiveSchedule).filter(ExecutiveSchedule.tenant_id == tenant_id)
    row = query.with_for_update(skip_locked=True).first() if lock else query.first()
    if row is not None:
        return row
    exists = db.query(ExecutiveSchedule.tenant_id).filter(ExecutiveSchedule.tenant_id == tenant_id).first()
    if exists is not None:
        return None  # locked by someone else
    row = ExecutiveSchedule(
        tenant_id=tenant_id, digest_enabled=True, digest_hour=8, digest_weekdays=[1, 2, 3, 4, 5], alerts_enabled=True,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    if lock:
        return query.with_for_update(skip_locked=True).first()
    return row


def serialize_schedule(row: ExecutiveSchedule) -> dict[str, Any]:
    return {
        "digest_enabled": row.digest_enabled,
        "digest_hour": row.digest_hour,
        "digest_weekdays": sorted(int(day) for day in row.digest_weekdays or []),
        "alerts_enabled": row.alerts_enabled,
        "last_digest_on": row.last_digest_on.isoformat() if row.last_digest_on else None,
        "last_alert_check_at": row.last_alert_check_at.isoformat() if row.last_alert_check_at else None,
    }


def save_schedule(db: Session, actor: User, change: dict[str, Any]) -> ExecutiveSchedule:
    from fastapi import HTTPException

    row = get_schedule(db, actor.tenant_id)
    if row is None:
        raise HTTPException(status_code=409, detail="Lịch bản tin đang được cập nhật, vui lòng thử lại")
    if "digest_hour" in change:
        hour = int(change["digest_hour"])
        if not 0 <= hour <= 23:
            raise HTTPException(status_code=422, detail="Giờ gửi bản tin phải từ 0 đến 23")
        row.digest_hour = hour
    if "digest_weekdays" in change:
        days = sorted({int(day) for day in change["digest_weekdays"] or []})
        if any(day not in WEEKDAYS for day in days):
            raise HTTPException(status_code=422, detail="Ngày gửi bản tin phải là thứ Hai (1) đến Chủ nhật (7)")
        row.digest_weekdays = days
    for flag in ("digest_enabled", "alerts_enabled"):
        if flag in change:
            setattr(row, flag, bool(change[flag]))
    row.updated_by_id = actor.id
    db.flush()
    return row


def notify_alerts(db: Session, user: User, alerts: list[dict[str, Any]]) -> int:
    """One notification per alert, at most once a day for the same subject."""
    today = local_today(db, user).isoformat()
    sent = 0
    for alert in alerts:
        created = create_notification(
            db,
            user=user,
            event_type="EXECUTIVE_ALERT",
            title=alert["title"],
            message=alert["message"],
            severity=alert["severity"],
            entity_type="EXECUTIVE",
            entity_id="alerts",
            payload={"rule": alert["rule"], "link": alert["link"]},
            dedup_key=f"exec-alert:{alert['rule']}:{alert['subject']}:{today}"[:255],
        )
        sent += created is not None
    return sent


def send_digest(db: Session, user: User, alert_count: int = 0) -> bool:
    digest = build_digest(db, user, alert_count)
    created = create_notification(
        db,
        user=user,
        event_type="EXECUTIVE_DIGEST",
        title=digest["title"],
        message=digest["message"],
        entity_type="EXECUTIVE",
        entity_id="overview",
        payload={"lines": digest["lines"], "date": digest["today"]},
        dedup_key=f"exec-digest:{digest['today']}",
    )
    return created is not None


def run_tenant(db: Session, tenant_id: uuid.UUID, now: datetime) -> dict[str, int]:
    done = {"alerts": 0, "digests": 0}
    schedule = get_schedule(db, tenant_id, lock=True)
    if schedule is None:
        return done
    people = recipients(db, tenant_id)
    if not people:
        return done
    alert_counts: dict[uuid.UUID, int] = {}
    check_alerts = schedule.alerts_enabled and (
        schedule.last_alert_check_at is None or now - schedule.last_alert_check_at >= ALERT_INTERVAL
    )
    settings = rule_settings(db, tenant_id) if schedule.alerts_enabled else []
    if check_alerts:
        for person in people:
            alerts = evaluate_alerts(db, person, settings)
            alert_counts[person.id] = len(alerts)
            done["alerts"] += notify_alerts(db, person, alerts)
        schedule.last_alert_check_at = now
    local_now = now.astimezone(tenant_zone(db, people[0]))
    due = (
        schedule.digest_enabled
        and local_now.isoweekday() in {int(day) for day in schedule.digest_weekdays or []}
        and local_now.hour >= schedule.digest_hour
        and schedule.last_digest_on != local_now.date()
    )
    if due:
        for person in people:
            count = alert_counts.get(person.id)
            if count is None:
                count = len(evaluate_alerts(db, person, settings)) if schedule.alerts_enabled else 0
            done["digests"] += send_digest(db, person, count)
        schedule.last_digest_on = local_now.date()
    return done


def run_due(db: Session, now: datetime | None = None) -> dict[str, int]:
    """One pass over every tenant; a tenant that fails is logged and skipped."""
    now = now or datetime.now(timezone.utc)
    total = {"alerts": 0, "digests": 0}
    for (tenant_id,) in db.query(Tenant.id).all():
        try:
            done = run_tenant(db, tenant_id, now)
            db.commit()
        except Exception:  # noqa: BLE001 - one tenant's bad data must not stop the others
            db.rollback()
            logger.exception("Executive schedule failed for tenant %s", tenant_id)
            continue
        for key, value in done.items():
            total[key] += value
    return total
