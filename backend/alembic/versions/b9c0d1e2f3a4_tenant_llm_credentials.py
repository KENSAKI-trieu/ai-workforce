"""API keys a company enters on its settings page, one per model vendor

`tenant_llm_credentials` holds each key sealed (app.core.llm_credentials) with its last
characters beside it for display. A vendor with a row uses the company's key instead of
the deployment's own.

The table is created only when missing: the test suite's create_all may have made it on a
developer's database first.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "tenant_llm_credentials" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "tenant_llm_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("sealed_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(12), nullable=False),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_tenant_llm_credential"),
    )


def downgrade() -> None:
    op.drop_table("tenant_llm_credentials")
