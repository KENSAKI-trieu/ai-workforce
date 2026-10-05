"""ai_agents.model_name: NULL means "the AI service's default model"

The column was stored and shown on the configuration page, but no part of the runtime
ever read it: every agent ran on the AI service's configured default whatever it said.
It is now honoured, so the values it holds would suddenly take effect -- every seeded row
says `gpt-4o`, a vendor the dev environment has no credit for. They are cleared to NULL,
which keeps each agent on exactly the model it has really been running on; an
administrator picks a model explicitly from now on.

Revision ID: fd4e5f6a7b83
Revises: fc3d4e5f6a72
"""

from alembic import op
import sqlalchemy as sa


revision = "fd4e5f6a7b83"
down_revision = "fc3d4e5f6a72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ai_agents", "model_name", existing_type=sa.String(length=100), nullable=True)
    op.execute("UPDATE ai_agents SET model_name = NULL")


def downgrade() -> None:
    op.execute("UPDATE ai_agents SET model_name = 'gpt-4o' WHERE model_name IS NULL")
    op.alter_column("ai_agents", "model_name", existing_type=sa.String(length=100), nullable=False)
