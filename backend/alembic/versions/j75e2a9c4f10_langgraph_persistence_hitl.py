"""add LangGraph persistence and HITL linkage

Revision ID: j75e2a9c4f10
Revises: i64e1f8a9b53
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "j75e2a9c4f10"
down_revision: Union[str, None] = "i64e1f8a9b53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_approvals",
        sa.Column("langgraph_interrupt_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "workflow_approvals",
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workflow_approvals",
        sa.Column("resume_error", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_workflow_approval_langgraph_interrupt",
        "workflow_approvals",
        ["langgraph_interrupt_id"],
    )
    op.execute("UPDATE chat_conversations SET thread_id = id::text")
    op.create_unique_constraint(
        "uq_chat_conversation_thread_id",
        "chat_conversations",
        ["thread_id"],
    )
    op.create_index(
        "idx_agent_workflows_tenant_thread",
        "agent_workflows",
        ["tenant_id", "thread_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_agent_workflows_tenant_thread", table_name="agent_workflows")
    op.drop_constraint(
        "uq_chat_conversation_thread_id",
        "chat_conversations",
        type_="unique",
    )
    op.drop_constraint(
        "uq_workflow_approval_langgraph_interrupt",
        "workflow_approvals",
        type_="unique",
    )
    op.drop_column("workflow_approvals", "resume_error")
    op.drop_column("workflow_approvals", "resumed_at")
    op.drop_column("workflow_approvals", "langgraph_interrupt_id")
