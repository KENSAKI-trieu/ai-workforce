"""marketing_campaigns + the Marketing permission boxes

The Marketing Agent writes a campaign in stages -- outline, then three posts and their
fact-check -- stopping for its author after each, and the row is where a campaign waits.

Three boxes come with it, backfilled so that nobody loses or gains more than the default
tree would give them:
- marketing.campaign.create: every position but guests, like finance.sheet.analyze;
- marketing.content.approve: positions that sign approvals (approvals.sign);
- marketing.campaign.view_all: positions that sign critical approvals.
None of them is an administrative or managerial marker, so no `users.role` changes.

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b2c3d4e5f6a8"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None

CREATE = "marketing.campaign.create"
APPROVE = "marketing.content.approve"
VIEW_ALL = "marketing.campaign.view_all"


def _codes(value) -> set[str]:
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return {str(code) for code in (value or [])}


def _set(bind, position_id, codes: set[str]) -> None:
    bind.execute(
        sa.text("UPDATE positions SET permissions = CAST(:perms AS JSONB) WHERE id = :id"),
        {"perms": json.dumps(sorted(codes)), "id": position_id},
    )


def upgrade() -> None:
    op.create_table(
        "marketing_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        # Fernet tokens, as in contract_reviews.
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False, server_default="OUTLINE_PENDING"),
        sa.Column("outline", sa.Text(), nullable=True),
        sa.Column("outline_feedback", sa.Text(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("drafts", postgresql.JSONB(), nullable=False),
        sa.Column("fact_check_report", postgresql.JSONB(), nullable=True),
        sa.Column("refine_rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_approvals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_marketing_campaigns_tenant_creator",
        "marketing_campaigns",
        ["tenant_id", "created_by_id", "created_at"],
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, slug, permissions FROM positions")).all()
    for position_id, slug, permissions in rows:
        codes = _codes(permissions)
        added = set(codes)
        if slug != "guest":
            added.add(CREATE)
        if "approvals.sign" in codes or "approvals.sign_critical" in codes:
            added.add(APPROVE)
        if "approvals.sign_critical" in codes:
            added.add(VIEW_ALL)
        if added != codes:
            _set(bind, position_id, added)


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, permissions FROM positions")).all()
    for position_id, permissions in rows:
        codes = _codes(permissions)
        kept = codes - {CREATE, APPROVE, VIEW_ALL}
        if kept != codes:
            _set(bind, position_id, kept)
    op.drop_index("idx_marketing_campaigns_tenant_creator", table_name="marketing_campaigns")
    op.drop_table("marketing_campaigns")
