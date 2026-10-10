"""Agents read only ticked knowledge; Marketing gets a skill shelf

Until now an agent whose `knowledge_access` was empty -- every agent nobody had configured
-- read the whole knowledge base, and "*" said so explicitly. An agent now reads exactly
what is ticked for it. So that no agent loses its documents today, each one that was
empty or "*" is ticked onto every collection its tenant has right now. A collection
created later must be ticked by an administrator.

Also adds `ai_agents.skill_access` (the shelf of know-how an agent writes with, used by
Marketing) and `marketing_campaigns.skills` (what a campaign read from it).

The downgrade drops the two columns. It does not put "empty" back: an explicit list of
every collection reads the same under the old rule.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def _selectors(value) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def upgrade() -> None:
    op.add_column(
        "ai_agents",
        sa.Column("skill_access", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "marketing_campaigns",
        sa.Column("skills", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    bind = op.get_bind()
    collections: dict[str, list[str]] = {}
    for tenant_id, name in bind.execute(
        sa.text("SELECT DISTINCT tenant_id, collection_name FROM document_chunks")
    ).all():
        collections.setdefault(str(tenant_id), []).append(f"collection:{name}")
    for agent_id, tenant_id, access in bind.execute(
        sa.text("SELECT id, tenant_id, knowledge_access FROM ai_agents")
    ).all():
        selectors = _selectors(access)
        if selectors and "*" not in selectors:
            continue
        ticked = sorted(collections.get(str(tenant_id), [])) or ["none"]
        bind.execute(
            sa.text("UPDATE ai_agents SET knowledge_access = CAST(:access AS JSONB) WHERE id = :id"),
            {"access": json.dumps(ticked), "id": agent_id},
        )


def downgrade() -> None:
    op.drop_column("marketing_campaigns", "skills")
    op.drop_column("ai_agents", "skill_access")
