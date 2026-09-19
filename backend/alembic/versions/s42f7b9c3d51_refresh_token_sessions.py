"""Record issued refresh tokens so sessions can be revoked

Refresh tokens were stateless JWTs. Nothing on the server knew a session existed,
so `/auth/logout` could only clear a cookie, rotation left the superseded token
valid until its natural expiry, and a stolen token was usable for thirty days
with no way to notice or stop it.

One row per issued token. `family_id` chains a login to every token later rotated
from it: when a token that has already been used is presented again, two parties
hold the same credential, and the whole family is revoked rather than just the
replayed row -- otherwise the thief and the victim would simply keep rotating
past each other.

The primary key is the JWT's own `jti`, so a presented token maps to its row
without a lookup table. `replaced_by_id` uses SET NULL because deleting a
superseding row must not cascade backwards through a rotation chain.

Purely additive. Existing refresh tokens have no row here and stop being accepted
once this ships, so every session is asked to log in again -- deliberate, since
those tokens were also handed out in response bodies and cannot be trusted.

Revision ID: s42f7b9c3d51
Revises: r31e6a8b2c40
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s42f7b9c3d51"
down_revision = "r31e6a8b2c40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=50), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["refresh_tokens.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("idx_refresh_tokens_user", "refresh_tokens", ["user_id"])
    op.create_index("idx_refresh_tokens_family", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("idx_refresh_tokens_family", table_name="refresh_tokens")
    op.drop_index("idx_refresh_tokens_user", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
