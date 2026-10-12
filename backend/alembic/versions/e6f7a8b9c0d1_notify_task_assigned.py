"""TASK_ASSIGNED notifications, on for everyone who already has preferences

A user's preferences store the event types they receive as a list written when the row
was created, so a type added afterwards is off for every existing user without anyone
having chosen that. Being handed a task is something everyone should hear about: the new
type is ticked on in every stored list. Someone can still switch it off on the
Notifications page.

The downgrade takes it out again.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None

EVENT = "TASK_ASSIGNED"


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
    for preference_id, types in bind.execute(
        sa.text("SELECT id, enabled_event_types FROM notification_preferences")
    ).all():
        current = _types(types)
        # An empty list reads as "every type" (notification_service), so it is left alone.
        if current and EVENT not in current:
            _set(bind, preference_id, [*current, EVENT])


def downgrade() -> None:
    bind = op.get_bind()
    for preference_id, types in bind.execute(
        sa.text("SELECT id, enabled_event_types FROM notification_preferences")
    ).all():
        current = _types(types)
        if EVENT in current:
            _set(bind, preference_id, [item for item in current if item != EVENT])
