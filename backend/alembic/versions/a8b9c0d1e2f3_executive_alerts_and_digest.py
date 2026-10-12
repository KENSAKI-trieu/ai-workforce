"""the CEO assistant's proactive alerts and scheduled digest

`executive_alert_rules` records a tenant's choice for each alert rule (on/off, threshold);
the rules themselves are code. `executive_schedules` holds when the digest goes out and
when alerts were last checked, one row per tenant.

Two notification types come with them, EXECUTIVE_ALERT and EXECUTIVE_DIGEST. A user's
preferences store the types they receive as a list written when the row was created, so
both are ticked on in every stored list, as TASK_ASSIGNED was (e6f7a8b9c0d1). Only
positions holding "Dùng Trợ lý CEO" ever receive them.

The tables are created only when missing: the test suite's create_all may have made them
on a developer's database first.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None

EVENTS = ("EXECUTIVE_ALERT", "EXECUTIVE_DIGEST")


def _types(value) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return [str(item) for item in (value or [])]


def _set(bind, preference_id, types: list[str]) -> None:
    bind.execute(
        sa.text("UPDATE notification_preferences SET enabled_event_types = CAST(:types AS JSONB) WHERE id = :id"),
        {"types": json.dumps(types), "id": preference_id},
    )


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "executive_alert_rules" not in existing:
        op.create_table(
            "executive_alert_rules",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("rule_key", sa.String(50), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("threshold", sa.Numeric(18, 2)),
            sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "rule_key", name="uq_executive_alert_rule"),
        )
    if "executive_schedules" not in existing:
        op.create_table(
            "executive_schedules",
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("digest_hour", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("digest_weekdays", postgresql.JSONB(), nullable=False, server_default=sa.text("'[1, 2, 3, 4, 5]'::jsonb")),
            sa.Column("alerts_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_digest_on", sa.Date()),
            sa.Column("last_alert_check_at", sa.DateTime(timezone=True)),
            sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    for preference_id, types in bind.execute(
        sa.text("SELECT id, enabled_event_types FROM notification_preferences")
    ).all():
        current = _types(types)
        # An empty list reads as "every type" (notification_service), so it is left alone.
        missing = [event for event in EVENTS if event not in current]
        if current and missing:
            _set(bind, preference_id, [*current, *missing])


def downgrade() -> None:
    bind = op.get_bind()
    for preference_id, types in bind.execute(
        sa.text("SELECT id, enabled_event_types FROM notification_preferences")
    ).all():
        current = _types(types)
        if any(event in current for event in EVENTS):
            _set(bind, preference_id, [item for item in current if item not in EVENTS])
    op.drop_table("executive_schedules")
    op.drop_table("executive_alert_rules")
