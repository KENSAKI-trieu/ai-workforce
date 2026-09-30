"""keep an uploaded contract's file and form so revisions can be written into it

A review kept only the contract's text, so an accepted revision could go no further than
a redline report. The uploaded file is now stored (sealed) with its form -- the layout,
styles and positions recorded before parsing -- and the file with the revisions written
in is kept beside it.

Revision ID: x97e2a4b8c06
Revises: w86d1f3a7b95
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "x97e2a4b8c06"
down_revision = "w86d1f3a7b95"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contract_reviews", sa.Column("original_storage_key", sa.Text(), nullable=True))
    op.add_column("contract_reviews", sa.Column("original_filename", sa.String(length=255), nullable=True))
    op.add_column("contract_reviews", sa.Column("original_format", sa.String(length=10), nullable=True))
    op.add_column("contract_reviews", sa.Column("form_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column("contract_reviews", sa.Column("revised_storage_key", sa.Text(), nullable=True))
    op.add_column("contract_reviews", sa.Column("revised_filename", sa.String(length=255), nullable=True))
    op.add_column("contract_reviews", sa.Column("revision_report", postgresql.JSONB(), nullable=True))
    op.add_column("contract_reviews", sa.Column("revised_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in (
        "revised_at", "revision_report", "revised_filename", "revised_storage_key",
        "form_snapshot", "original_format", "original_filename", "original_storage_key",
    ):
        op.drop_column("contract_reviews", column)
