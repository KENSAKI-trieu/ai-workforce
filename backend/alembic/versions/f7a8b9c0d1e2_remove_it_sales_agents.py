"""remove the IT and Sales AI Employees

Both ran on placeholder logic and were never opened for chat; they are gone from the
product. Each tenant's two rows are deleted, with their conversations (the foreign key
cascades). A task that named one of them keeps its record and loses only the link. Plugin
packages, agent budgets and routing rules aimed at them have nothing left to act on.

The IT and SALES *departments* are untouched: they are a company's own org units.

The downgrade does nothing: the rows and their conversations cannot be brought back, and
ensure_tenant_default_agents no longer seeds these roles.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None

ROLES = "('IT', 'SALES')"

# Table -> statement, in order: the links to an agent row go before the row itself. Some
# of these tables are created by the app at startup rather than by a migration, so a
# fresh database may not have them yet -- and then has nothing in them to delete.
STATEMENTS = (
    (
        "tasks",
        f"UPDATE tasks SET ai_agent_id = NULL WHERE ai_agent_id IN "
        f"(SELECT id FROM ai_agents WHERE role_code IN {ROLES})",
    ),
    ("tenant_plugin_installs", f"DELETE FROM tenant_plugin_installs WHERE upper(target_role) IN {ROLES}"),
    ("tenant_plugins", f"DELETE FROM tenant_plugins WHERE upper(target_role) IN {ROLES}"),
    ("model_routing_rules", f"DELETE FROM model_routing_rules WHERE agent_role IN {ROLES}"),
    ("cost_budgets", f"DELETE FROM cost_budgets WHERE scope_type = 'AGENT' AND scope_id IN {ROLES}"),
    (
        "integration_connections",
        f"""
        UPDATE integration_connections
        SET allowed_agent_roles = COALESCE(
            (SELECT jsonb_agg(role) FROM jsonb_array_elements(allowed_agent_roles) AS role
             WHERE role #>> '{{}}' NOT IN {ROLES}),
            '[]'::jsonb
        )
        WHERE allowed_agent_roles ?| array['IT', 'SALES']
        """,
    ),
    ("ai_agents", f"DELETE FROM ai_agents WHERE role_code IN {ROLES}"),
)


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table, statement in STATEMENTS:
        if table in existing:
            op.execute(statement)


def downgrade() -> None:
    pass
