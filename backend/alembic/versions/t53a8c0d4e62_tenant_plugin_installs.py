"""record which prompt/skill plugin packages a tenant enabled

Revision ID: t53a8c0d4e62
Revises: s42f7b9c3d51
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "t53a8c0d4e62"
down_revision = "s42f7b9c3d51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_plugin_installs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plugin_name", sa.String(length=100), nullable=False),
        sa.Column("plugin_version", sa.String(length=50), nullable=False),
        sa.Column("target_role", sa.String(length=50), nullable=False),
        sa.Column(
            "installed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "plugin_name", name="uq_tenant_plugin"),
    )
    op.create_index(
        "idx_tenant_plugin_role",
        "tenant_plugin_installs",
        ["tenant_id", "target_role"],
    )


def downgrade() -> None:
    op.drop_index("idx_tenant_plugin_role", table_name="tenant_plugin_installs")
    op.drop_table("tenant_plugin_installs")
