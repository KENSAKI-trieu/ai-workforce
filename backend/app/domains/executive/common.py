"""Small helpers the executive tools share: the company's own today, and name matching."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.models.models import Tenant, User

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"


def tenant_zone(db: Session, user: User) -> ZoneInfo:
    # Read through the session: a streamed turn may hold a detached user.
    name = str(db.query(Tenant.timezone).filter(Tenant.id == user.tenant_id).scalar() or DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_today(db: Session, user: User) -> date:
    """Today where the company is, not where the server is."""
    return datetime.now(tenant_zone(db, user)).date()


def local_date(value: datetime | None, zone: ZoneInfo) -> date | None:
    if value is None:
        return None
    return value.astimezone(zone).date() if value.tzinfo else value.date()


def fold(value: str | None) -> str:
    """Lower case without accents, đ as d: "Trần Thị Lan" and "tran thi lan" match."""
    text = unicodedata.normalize("NFD", str(value or "").replace("đ", "d").replace("Đ", "D"))
    return " ".join("".join(char for char in text if unicodedata.category(char) != "Mn").lower().split())
