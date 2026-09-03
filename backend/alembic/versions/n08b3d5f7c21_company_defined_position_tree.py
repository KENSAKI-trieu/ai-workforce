"""Company-defined position tree replacing the hardcoded role vocabulary.

Creates `positions`, links users to it, drops the `ck_users_role` CHECK constraint (the
same step departments got in b72e4f910c31), and backfills every existing tenant with a
default tree that reproduces today's behaviour exactly.

The seed data below is deliberately frozen as literals rather than imported from
app.core.permissions: a migration is a historical record, and it must keep producing the
same rows even after the permission vocabulary evolves.

Revision ID: n08b3d5f7c21
Revises: m97a2c4e6b10
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "n08b3d5f7c21"
down_revision = "m97a2c4e6b10"
branch_labels = None
depends_on = None


# --- frozen seed, matching app.core.permissions at the time of this migration ---
_MANAGER = [
    "analytics.view",
    "approvals.sign",
    "audit.view",
    "costs.view",
    "finance.expense.view",
    "hr.contract.view",
    "hr.directory.view",
    "hr.performance.view",
    "knowledge.manage",
    "users.view",
]
_ADMIN = sorted(set(_MANAGER) | {
    "agents.configure",
    "approvals.sign_critical",
    "audit.view_all",
    "costs.manage",
    "knowledge.view_restricted",
    "legal.document.generate",
    "users.manage",
    "users.position.assign",
    "workspace.settings.manage",
})
_CEO = sorted(set(_ADMIN) | {
    "hr.compensation.view",
    "hr.employee.manage",
    "hr.private.view",
    "hr.scope.company",
    "org.structure.manage",
})

# (slug, name, legacy role, parent slug, permissions, grants_all)
_DEFAULT_TREE = [
    ("owner", "CEO", "Owner", None, [], True),
    ("ceo", "Giám đốc điều hành", "CEO", "owner", _CEO, False),
    ("admin", "Quản trị viên", "Admin", "ceo", _ADMIN, False),
    ("manager", "Quản lý", "Manager", "ceo", _MANAGER, False),
    ("employee", "Nhân viên", "Employee", "manager", [], False),
    ("guest", "Khách", "Guest", "manager", [], False),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "positions" not in set(inspector.get_table_names()):
        op.create_table(
            "positions",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "parent_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("positions.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column("name", sa.String(length=150), nullable=False),
            sa.Column("slug", sa.String(length=80), nullable=False),
            sa.Column(
                "permissions",
                sa.dialects.postgresql.JSONB(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column(
                "grants_all", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
            sa.Column("default_department", sa.String(length=50), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("tenant_id", "slug", name="uq_position_tenant_slug"),
        )
        op.create_index(
            "idx_positions_tenant_parent", "positions", ["tenant_id", "parent_id"]
        )

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "position_id" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "position_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("positions.id", ondelete="RESTRICT"),
                nullable=True,
            ),
        )
    if "manager_is_manual" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "manager_is_manual",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    # The role vocabulary is tenant data now, not a fixed code-level list.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")

    _backfill(bind)


def _backfill(bind) -> None:
    """Seed the default tree per tenant and place every user in it. Idempotent."""
    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants")).all()]
    for tenant_id in tenant_ids:
        existing = {
            row[0]: row[1]
            for row in bind.execute(
                sa.text("SELECT slug, id FROM positions WHERE tenant_id = :tenant"),
                {"tenant": tenant_id},
            ).all()
        }
        for order, (slug, name, _role, parent_slug, permissions, grants_all) in enumerate(
            _DEFAULT_TREE
        ):
            if slug in existing:
                continue
            position_id = uuid.uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO positions "
                    "(id, tenant_id, parent_id, name, slug, permissions, grants_all,"
                    " is_active, sort_order) "
                    "VALUES (:id, :tenant, :parent, :name, :slug, CAST(:perms AS JSONB),"
                    " :grants_all, true, :sort_order)"
                ),
                {
                    "id": position_id,
                    "tenant": tenant_id,
                    "parent": existing.get(parent_slug) if parent_slug else None,
                    "name": name,
                    "slug": slug,
                    "perms": json.dumps(permissions),
                    "grants_all": grants_all,
                    "sort_order": order,
                },
            )
            existing[slug] = position_id

        for slug, _name, legacy_role, _parent, _perms, _grants in _DEFAULT_TREE:
            bind.execute(
                sa.text(
                    "UPDATE users SET position_id = :position "
                    "WHERE tenant_id = :tenant AND position_id IS NULL AND role = :role"
                ),
                {"position": existing[slug], "tenant": tenant_id, "role": legacy_role},
            )
        # Anything with an unrecognised legacy role lands on the least-privileged node
        # rather than being left without a position.
        bind.execute(
            sa.text(
                "UPDATE users SET position_id = :position "
                "WHERE tenant_id = :tenant AND position_id IS NULL"
            ),
            {"position": existing["employee"], "tenant": tenant_id},
        )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK "
        "(role IN ('Owner', 'Admin', 'Manager', 'Employee', 'CEO', 'Guest'))"
    )
    with op.batch_alter_table("users") as batch:
        batch.drop_column("manager_is_manual")
        batch.drop_column("position_id")
    op.drop_index("idx_positions_tenant_parent", table_name="positions")
    op.drop_table("positions")
