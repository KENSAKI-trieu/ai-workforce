"""Rename the founder's legacy role string from "Owner" to "CEO".

The person who creates a company holds the root position, whose display name has always
been "CEO". The stored `users.role` string said "Owner", so the same person was called two
different things depending on which guard was asked. This makes the string match the job.

Document ACLs persist role strings as data and match them lowercased
(`documents._chunk_visible_to_user`), so anything that granted access to "owner" also
grants it to "ceo" after this runs -- otherwise the rename would silently revoke documents
that were deliberately shared with the founder.

Frozen literals, not imports from app.core.permissions: a migration must keep producing
the same rows after the vocabulary evolves.

Revision ID: q20d5f7b9e43
Revises: p19c4e6a8d32
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q20d5f7b9e43"
down_revision = "p19c4e6a8d32"
branch_labels = None
depends_on = None


# Appends `new` to every ACL that already mentions `old`, rather than replacing it. Both
# strings then match, which keeps the change reversible: dropping `new` again cannot
# revoke a grant that was written against `new` in the first place. Matched case-insensitively
# because that is how the readers compare. De-duplicated, so a re-run is a no-op.
_WIDEN_ACL = """
UPDATE {table}
SET allowed_roles = (
    SELECT COALESCE(jsonb_agg(DISTINCT value), '[]'::jsonb)
    FROM (
        SELECT elem AS value FROM jsonb_array_elements_text(allowed_roles) AS elem
        UNION
        SELECT '{new}'
    ) AS widened
)
WHERE EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(allowed_roles) AS elem
    WHERE lower(elem) = '{old}'
)
"""

# Removes `drop` from every ACL that also carries `keep`, undoing _WIDEN_ACL without
# touching an ACL that only ever named `drop`.
_NARROW_ACL = """
UPDATE {table}
SET allowed_roles = (
    SELECT COALESCE(jsonb_agg(DISTINCT elem), '[]'::jsonb)
    FROM jsonb_array_elements_text(allowed_roles) AS elem
    WHERE lower(elem) <> '{drop}'
)
WHERE EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(allowed_roles) AS elem
    WHERE lower(elem) = '{drop}'
)
AND EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(allowed_roles) AS elem
    WHERE lower(elem) = '{keep}'
)
"""

_ACL_TABLES = ("knowledge_documents", "document_chunks")


def _acl_tables() -> list[str]:
    """Only the ACL tables this database actually has.

    `init_db.create_all` and the migration chain can be at different points on a developer
    machine, and a missing table here is not a reason to fail the whole upgrade.
    """
    inspector = sa.inspect(op.get_bind())
    return [table for table in _ACL_TABLES if inspector.has_table(table)]


def upgrade() -> None:
    op.execute(sa.text("UPDATE users SET role = 'CEO' WHERE role = 'Owner'"))
    for table in _acl_tables():
        op.execute(sa.text(_WIDEN_ACL.format(table=table, old="owner", new="ceo")))


def downgrade() -> None:
    # Only users holding the root position were Owners before this migration. Users whose
    # role was already "CEO" sat on the `ceo` position instead, so keying on the position
    # is what makes this reversible without demoting the wrong people.
    op.execute(sa.text(
        """
        UPDATE users SET role = 'Owner'
        WHERE role = 'CEO'
          AND position_id IN (SELECT id FROM positions WHERE grants_all IS TRUE)
        """
    ))
    for table in _acl_tables():
        op.execute(sa.text(_NARROW_ACL.format(table=table, drop="ceo", keep="owner")))
