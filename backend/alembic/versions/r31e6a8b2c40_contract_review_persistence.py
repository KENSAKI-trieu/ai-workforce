"""Persist contract reviews and per-finding reviewer decisions

Until now a contract review existed only as an HTTP response. The reviewer's
Accept/Reject/Edit choices lived in browser state and were lost on refresh, and
nothing recorded who had accepted which clause -- for an artifact whose whole
purpose is legal accountability.

Two tables rather than one. `contract_reviews` keeps the analyzer output whole as
JSONB because it is immutable and already versioned by `review_version`;
normalizing findings into rows would force a migration every time a rule pack
changes. `contract_review_decisions` is rows rather than a blob on the parent
because decisions are an audit trail: each needs its own author and timestamp,
two reviewers must not overwrite one another, and "what did this person accept"
has to be answerable.

`workflow_id` uses SET NULL: deleting an escalation workflow must not erase the
record of what was reviewed. The unique key on (tenant_id, idempotency_key) makes
re-uploading the same contract resume the existing review with its decisions
intact, and carries `review_version` so an analyzer upgrade starts a fresh review
rather than attaching old decisions to renumbered findings.

Purely additive: nothing was ever persisted, so there is no data to backfill.

Revision ID: r31e6a8b2c40
Revises: q20d5f7b9e43
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r31e6a8b2c40"
down_revision = "q20d5f7b9e43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contract_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="UPLOAD"),
        sa.Column("document_name", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("represented_party", sa.String(length=20), nullable=False),
        sa.Column("contract_type", sa.String(length=80), nullable=False),
        sa.Column("review_version", sa.String(length=10), nullable=False, server_default="2.0"),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="LOW"),
        sa.Column("total_findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contract_text", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="OPEN"),
        sa.Column("redline_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("redline_storage_key", sa.Text(), nullable=True),
        sa.Column("redline_filename", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["agent_workflows.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_contract_review_idempotency"
        ),
    )
    op.create_index(
        "idx_contract_reviews_tenant_created", "contract_reviews", ["tenant_id", "created_at"]
    )
    op.create_index(
        "idx_contract_reviews_tenant_creator", "contract_reviews", ["tenant_id", "created_by_id"]
    )

    op.create_table(
        "contract_review_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_key", sa.String(length=64), nullable=False),
        sa.Column("finding_ref", sa.String(length=40), nullable=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("revised_text", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["review_id"], ["contract_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_id", "finding_key", name="uq_contract_review_decision_finding"
        ),
    )
    op.create_index(
        "idx_contract_review_decisions_review", "contract_review_decisions", ["review_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_contract_review_decisions_review", table_name="contract_review_decisions")
    op.drop_table("contract_review_decisions")
    op.drop_index("idx_contract_reviews_tenant_creator", table_name="contract_reviews")
    op.drop_index("idx_contract_reviews_tenant_created", table_name="contract_reviews")
    op.drop_table("contract_reviews")
