"""finance.sheet.analyze: the uploaded-spreadsheet analysis gets its own box

Analysing one's own spreadsheet shipped open to everyone signed in. It becomes a box on
the position like every other power, granted here to every position except guests so
that nobody loses what they had; an administrator can now untick it.

The box is no administrative or managerial marker, so no `users.role` changes.

Revision ID: fc3d4e5f6a72
Revises: fb2c3d4e5f61
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "fc3d4e5f6a72"
down_revision = "fb2c3d4e5f61"
branch_labels = None
depends_on = None

CODE = "finance.sheet.analyze"


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
    rows = bind.execute(sa.text("SELECT id, slug, permissions FROM positions")).all()
    for position_id, slug, permissions in rows:
        codes = _codes(permissions)
        if slug != "guest" and CODE not in codes:
            _set(bind, position_id, codes | {CODE})


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, permissions FROM positions")).all()
    for position_id, permissions in rows:
        codes = _codes(permissions)
        if CODE in codes:
            _set(bind, position_id, codes - {CODE})
