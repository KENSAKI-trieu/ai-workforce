"""executive.*: the CEO assistant's permission boxes

Two boxes for the CEO assistant, backfilled onto the seeded `ceo` position only -- the
same set a new tenant's tree gives it. The root needs nothing: it grants every permission.
Neither box is an administrative or managerial marker, so no `users.role` changes.

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a8
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b2c3d4e5f6a8"
branch_labels = None
depends_on = None

CODES = {"executive.briefing.view", "executive.alerts.manage"}


def _codes(value) -> set[str]:
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return {str(code) for code in (value or [])}


def _set(bind, position_id, codes: set[str]) -> None:
    bind.execute(
        sa.text("UPDATE positions SET permissions = CAST(:perms AS JSONB) WHERE id = :id"),
        {"perms": json.dumps(sorted(codes)), "id": position_id},
    )


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, permissions FROM positions WHERE slug = 'ceo'")
    ).all()
    for position_id, permissions in rows:
        codes = _codes(permissions)
        if not CODES <= codes:
            _set(bind, position_id, codes | CODES)


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, permissions FROM positions")).all()
    for position_id, permissions in rows:
        codes = _codes(permissions)
        if codes & CODES:
            _set(bind, position_id, codes - CODES)
