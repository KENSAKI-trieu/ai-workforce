"""finance sheets: spreadsheets a user uploads to analyse

Someone's own Excel or CSV -- a project cost sheet, a payroll export -- read into rows
the Finance agent and the /finance page compute on with fixed operations. Owned by the
uploader alone; nothing here is part of the books.

Revision ID: fb2c3d4e5f61
Revises: fa1b2c3d4e50
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "fb2c3d4e5f61"
down_revision = "fa1b2c3d4e50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fin_sheets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sheet_names", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("sheet_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("header_row", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("columns", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("rows", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("skipped_rows", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("skip_totals", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_fin_sheets_owner", "fin_sheets", ["tenant_id", "created_by_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_fin_sheets_owner", table_name="fin_sheets")
    op.drop_table("fin_sheets")
