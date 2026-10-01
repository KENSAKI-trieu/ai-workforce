"""org-structure permissions take over the Owner/Admin/CEO role checks agents still used

Three checks had no box on a position at all -- searching every department's knowledge,
reading every contract review, and approving legal documents -- and were answered by the
role string alone. They become permissions, granted here to every position that already
had them through its role: any position carrying an administrative power, which is what
made its holders Admin (or CEO) in the first place.

The legacy `users.role` string is then recomputed for everyone from their position. It
used to be written only when a person was assigned, so a position whose boxes were
changed later left its holders with the old role -- and every guard still reading the
role with the old access. The rule below is `permissions.legacy_role_for_position` as of
this revision, copied so that later changes to it cannot rewrite history.

Revision ID: y08f3b5c9d17
Revises: x97e2a4b8c06
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "y08f3b5c9d17"
down_revision = "x97e2a4b8c06"
branch_labels = None
depends_on = None

NEW_CODES = ("knowledge.scope.company", "legal.document.approve", "legal.review.view_all")

# Powers no Manager ever had; holding one made a position administrative.
ADMIN_MARKERS = {
    "users.manage",
    "users.position.assign",
    "workspace.settings.manage",
    "agents.configure",
    "knowledge.view_restricted",
    "approvals.sign_critical",
    "costs.manage",
    "audit.view_all",
    "legal.document.generate",
    "org.structure.manage",
    *NEW_CODES,
}
MANAGER_MARKERS = {"users.view", "knowledge.manage", "approvals.sign", "hr.scope.reports", "hr.scope.company"}


def _role(slug: str, grants_all: bool, codes: set[str]) -> str:
    if grants_all:
        return "CEO"
    if codes & ADMIN_MARKERS:
        classified = "Admin"
    elif codes & MANAGER_MARKERS:
        classified = "Manager"
    else:
        classified = "Employee"
    if slug == "ceo" and classified == "Admin":
        return "CEO"
    if slug == "guest" and classified == "Employee":
        return "Guest"
    return classified


def _codes(value) -> set[str]:
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return {str(code) for code in (value or [])}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, slug, grants_all, is_active, permissions FROM positions"
    )).all()
    for position_id, slug, grants_all, is_active, permissions in rows:
        codes = _codes(permissions)
        if not grants_all and codes & ADMIN_MARKERS:
            codes |= set(NEW_CODES)
            bind.execute(
                sa.text("UPDATE positions SET permissions = CAST(:perms AS JSONB) WHERE id = :id"),
                {"perms": json.dumps(sorted(codes)), "id": position_id},
            )
        effective = codes if is_active else set()
        bind.execute(
            sa.text("UPDATE users SET role = :role WHERE position_id = :id AND role <> :role"),
            {"role": _role(slug, bool(grants_all), effective), "id": position_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, permissions FROM positions")).all()
    for position_id, permissions in rows:
        codes = _codes(permissions)
        if codes & set(NEW_CODES):
            bind.execute(
                sa.text("UPDATE positions SET permissions = CAST(:perms AS JSONB) WHERE id = :id"),
                {"perms": json.dumps(sorted(codes - set(NEW_CODES))), "id": position_id},
            )
