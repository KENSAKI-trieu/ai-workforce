"""give each AI Employee an empty tenant prompt overlay

The existing `system_prompt` column is deliberately left in place and left alone. It was
editable through the UI for a long time while nothing read it, so its stored values are
a mix of seed text and abandoned edits. Copying that into a column the model now reads
would change how every existing tenant answers, so the overlay starts empty instead.

Revision ID: u64b9d1e5f73
Revises: t53a8c0d4e62
"""

from alembic import op
import sqlalchemy as sa


revision = "u64b9d1e5f73"
down_revision = "t53a8c0d4e62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_agents", sa.Column("prompt_overlay", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_agents", "prompt_overlay")
