"""agent_tool_outputs: an HR tool's reply and card, held for the chat turn

The HR agent's tools answer with personal data. Under LangGraph their results travel
through the AI service, which masks emails and phone numbers and keeps checkpoints, so
the executor stores the reply and card here and the backend puts them back into the chat
response. Rows are deleted as they are read.

Revision ID: a1b2c3d4e5f7
Revises: fd4e5f6a7b83
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a1b2c3d4e5f7"
down_revision = "fd4e5f6a7b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tool_outputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        # Fernet tokens, as in contract_reviews.
        sa.Column("reply", sa.Text(), nullable=False),
        sa.Column("cards", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_agent_tool_outputs_owner",
        "agent_tool_outputs",
        ["tenant_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_agent_tool_outputs_owner", table_name="agent_tool_outputs")
    op.drop_table("agent_tool_outputs")
