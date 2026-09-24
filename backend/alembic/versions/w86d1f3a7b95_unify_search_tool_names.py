"""unify the three knowledge-search tool names as rag_search

Knowledge search was `rag_search` at the tool gateway, `hybrid_rag_search` in the HR and
Legal chats and `hybrid_search_documents` in the Knowledge chat -- one capability, three
grants that could disagree. Every stored grant and every tenant-authored plugin package is
rewritten to `rag_search`.

Where the old names meant different things on one row, the merged grant follows the usual
rule: a deny wins. An agent whose Knowledge search had been switched off keeps search off
on every path, rather than getting it back through the gateway name.

The code keeps reading the old names as aliases (app/core/tool_permissions.py), so a row
or package this migration has not seen still works.

Revision ID: w86d1f3a7b95
Revises: v75c0e2f6a84
"""

import json
import re

from alembic import op
import sqlalchemy as sa


revision = "w86d1f3a7b95"
down_revision = "v75c0e2f6a84"
branch_labels = None
depends_on = None

RENAMED = {"hybrid_rag_search": "rag_search", "hybrid_search_documents": "rag_search"}
GRANT_COLUMNS = ("tools_access", "allowed_actions", "disallowed_actions")
_OLD_NAME = re.compile(r"\b(hybrid_rag_search|hybrid_search_documents)\b")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [str(item) for item in value]


def _renamed(names: list[str]) -> list[str]:
    return list(dict.fromkeys(RENAMED.get(name, name) for name in names))


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(f"SELECT id, {', '.join(GRANT_COLUMNS)} FROM ai_agents")
    ).mappings().all()
    for row in rows:
        updates = {}
        for column in GRANT_COLUMNS:
            current = _as_list(row[column])
            renamed = _renamed(current)
            if renamed != current:
                updates[column] = json.dumps(renamed)
        if updates:
            assignments = ", ".join(f"{column} = CAST(:{column} AS JSONB)" for column in updates)
            connection.execute(
                sa.text(f"UPDATE ai_agents SET {assignments} WHERE id = :id"),
                {**updates, "id": row["id"]},
            )

    plugins = connection.execute(sa.text("SELECT id, source_yaml FROM tenant_plugins")).mappings().all()
    for plugin in plugins:
        source = plugin["source_yaml"] or ""
        rewritten = _OLD_NAME.sub("rag_search", source)
        if rewritten != source:
            connection.execute(
                sa.text("UPDATE tenant_plugins SET source_yaml = :source WHERE id = :id"),
                {"source": rewritten, "id": plugin["id"]},
            )


def downgrade() -> None:
    # Only the agent rows can be put back: which spelling a tenant package used is not
    # recorded, and the older code accepts `rag_search` in a package anyway.
    connection = op.get_bind()
    old_name_by_role = {"HR": "hybrid_rag_search", "LEGAL": "hybrid_rag_search", "KNOWLEDGE": "hybrid_search_documents"}
    rows = connection.execute(
        sa.text(f"SELECT id, role_code, {', '.join(GRANT_COLUMNS)} FROM ai_agents")
    ).mappings().all()
    for row in rows:
        old_name = old_name_by_role.get(str(row["role_code"]).upper())
        if old_name is None:
            continue
        updates = {}
        for column in GRANT_COLUMNS:
            current = _as_list(row[column])
            if "rag_search" not in current:
                continue
            restored = [old_name if name == "rag_search" else name for name in current]
            # LEGAL and KNOWLEDGE also held `rag_search` as their gateway grant.
            if row["role_code"].upper() != "HR":
                restored.append("rag_search")
            updates[column] = json.dumps(list(dict.fromkeys(restored)))
        if updates:
            assignments = ", ".join(f"{column} = CAST(:{column} AS JSONB)" for column in updates)
            connection.execute(
                sa.text(f"UPDATE ai_agents SET {assignments} WHERE id = :id"),
                {**updates, "id": row["id"]},
            )
