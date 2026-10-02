"""A tenant's finance settings: its chart of accounts and who signs how much."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.domains.finance.charts import CHARTS, chart_rows
from app.domains.finance.money import to_decimal
from app.models.models import FinAccount, FinSettings

# Above the last bound an amount needs the critical signing permission. Matches the
# blueprint's default: an accountant below 20m, the chief accountant to 500m, the CFO above.
CRITICAL_PERMISSION = "approvals.sign_critical"
DEFAULT_THRESHOLDS: tuple[dict[str, Any], ...] = (
    {"up_to": 20_000_000, "permission": "finance.journal.approve"},
    {"up_to": 500_000_000, "permission": "finance.journal.approve_high"},
)
# Who may sign at each level. A higher level's signer may sign everything below it, so an
# amount of 5m does not wait for an accountant when only the chief accountant is in.
SIGNING_LADDER: tuple[str, ...] = (
    "finance.journal.approve",
    "finance.journal.approve_high",
    CRITICAL_PERMISSION,
)


def get_settings(db: Session, tenant_id: uuid.UUID) -> FinSettings:
    settings = db.get(FinSettings, tenant_id)
    if settings is None:
        settings = FinSettings(
            tenant_id=tenant_id,
            chart="TT200",
            approval_thresholds=[dict(item) for item in DEFAULT_THRESHOLDS],
            po_tolerance_percent=Decimal("0"),
        )
        db.add(settings)
        db.flush()
    return settings


def thresholds(settings: FinSettings) -> list[dict[str, Any]]:
    configured = settings.approval_thresholds or [dict(item) for item in DEFAULT_THRESHOLDS]
    return sorted(
        ({"up_to": to_decimal(item["up_to"]), "permission": item["permission"]} for item in configured),
        key=lambda item: item["up_to"],
    )


def validate_thresholds(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending bounds, each naming a finance signing permission below the critical one."""
    allowed = set(SIGNING_LADDER[:-1])
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        permission = str(item.get("permission") or "")
        if permission not in allowed:
            raise ValueError(f"Ngưỡng duyệt phải dùng một trong: {', '.join(sorted(allowed))}")
        bound = to_decimal(item.get("up_to"))
        if bound <= 0:
            raise ValueError("Ngưỡng duyệt phải lớn hơn 0")
        cleaned.append({"up_to": int(bound), "permission": permission})
    bounds = [item["up_to"] for item in cleaned]
    if bounds != sorted(bounds) or len(set(bounds)) != len(bounds):
        raise ValueError("Các ngưỡng duyệt phải tăng dần và không trùng nhau")
    return cleaned


def required_permission(settings: FinSettings, amount: Decimal) -> tuple[str, str]:
    """(permission, risk level) needed to approve `amount`. Bounds are inclusive."""
    for index, item in enumerate(thresholds(settings)):
        if amount <= item["up_to"]:
            return item["permission"], "MEDIUM" if index == 0 else "HIGH"
    return CRITICAL_PERMISSION, "CRITICAL"


def permissions_that_sign(permission: str) -> frozenset[str]:
    """Every permission that may sign at `permission`'s level: that one and those above."""
    if permission not in SIGNING_LADDER:
        return frozenset({permission, CRITICAL_PERMISSION})
    return frozenset(SIGNING_LADDER[SIGNING_LADDER.index(permission):])


def seed_chart(db: Session, tenant_id: uuid.UUID, chart: str) -> int:
    """Add the standard chart's accounts the tenant does not have yet. Never overwrites."""
    if chart not in CHARTS:
        raise ValueError(f"Unknown chart: {chart}")
    existing = {
        code for (code,) in db.query(FinAccount.code).filter(FinAccount.tenant_id == tenant_id)
    }
    added = 0
    for row in chart_rows(chart):
        if row["code"] in existing:
            continue
        db.add(FinAccount(tenant_id=tenant_id, is_active=True, **row))
        added += 1
    db.flush()
    return added
