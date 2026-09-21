"""let a tenant author its own plugin packages

Revision ID: v75c0e2f6a84
Revises: u64b9d1e5f73
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v75c0e2f6a84"
down_revision = "u64b9d1e5f73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_plugins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("target_role", sa.String(length=50), nullable=False),
        sa.Column("source_yaml", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_plugin_name"),
    )


def downgrade() -> None:
    op.drop_table("tenant_plugins")
