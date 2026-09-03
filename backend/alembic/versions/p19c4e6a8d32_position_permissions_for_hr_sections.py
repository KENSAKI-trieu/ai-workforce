"""Move HR data-section access from the role x department matrix onto position permissions.

The old rule cross-multiplied `actor.role` with hardcoded department codes, so an Admin in
HR and an Admin in IT were different jobs that one position could not express. This
migration splits those into real positions and moves the affected users, so every person
keeps exactly the sections they had. Folding them together instead would either strip HR
of data it has today or hand Finance data it never had.

Frozen literals, not imports from app.core.permissions: a migration must keep producing
the same rows after the vocabulary evolves.

Revision ID: p19c4e6a8d32
Revises: n08b3d5f7c21
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "p19c4e6a8d32"
down_revision = "n08b3d5f7c21"
branch_labels = None
depends_on = None


_MANAGER_CORE = [
    "analytics.view",
    "approvals.sign",
    "audit.view",
    "costs.view",
    "finance.expense.view",
    "hr.directory.view",
    "hr.scope.reports",
    "knowledge.manage",
    "users.view",
]
_ADMIN_CORE = sorted(set(_MANAGER_CORE) | {
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
_LINE_SECTIONS = ["hr.contract.view", "hr.leave.view", "hr.performance.view"]
_ALL_SECTIONS = [
    "hr.compensation.view",
    "hr.contract.view",
    "hr.directory.view",
    "hr.discipline.view",
    "hr.documents.view",
    "hr.leave.view",
    "hr.notes.view",
    "hr.performance.view",
    "hr.private.view",
]

_MANAGER = sorted(set(_MANAGER_CORE) | set(_LINE_SECTIONS))
_ADMIN = sorted(set(_ADMIN_CORE) | set(_LINE_SECTIONS))
_CEO = sorted(
    set(_ADMIN_CORE)
    | set(_ALL_SECTIONS)
    | {"org.structure.manage", "hr.scope.company", "hr.employee.manage"}
)

# Refreshed permission list for the positions seeded by the previous migration.
_REFRESH = {"ceo": _CEO, "admin": _ADMIN, "manager": _MANAGER}

# (slug, name, legacy role, legacy department, permissions)
_SPECIALISATIONS = [
    (
        "hr-admin", "Quản trị viên Nhân sự", "Admin", "HR",
        sorted(set(_ADMIN_CORE) | set(_ALL_SECTIONS) | {"hr.employee.manage"}),
    ),
    (
        "hr-manager", "Quản lý Nhân sự", "Manager", "HR",
        sorted(
            set(_MANAGER_CORE)
            | set(_LINE_SECTIONS)
            | {"hr.employee.manage", "hr.private.view", "hr.documents.view"}
        ),
    ),
    (
        "finance-admin", "Quản trị viên Tài chính", "Admin", "FINANCE",
        sorted(set(_ADMIN_CORE) | {"hr.compensation.view"}),
    ),
    (
        "finance-manager", "Quản lý Tài chính", "Manager", "FINANCE",
        sorted(set(_MANAGER_CORE) | {"hr.compensation.view"}),
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants")).all()]

    for tenant_id in tenant_ids:
        positions = {
            row[0]: row[1]
            for row in bind.execute(
                sa.text("SELECT slug, id FROM positions WHERE tenant_id = :tenant"),
                {"tenant": tenant_id},
            ).all()
        }
        if not positions:
            continue

        for slug, permissions in _REFRESH.items():
            if slug in positions:
                bind.execute(
                    sa.text(
                        "UPDATE positions SET permissions = CAST(:perms AS JSONB) "
                        "WHERE id = :id"
                    ),
                    {"perms": json.dumps(permissions), "id": positions[slug]},
                )

        parent_id = positions.get("ceo")
        for order, (slug, name, legacy_role, legacy_department, permissions) in enumerate(
            _SPECIALISATIONS
        ):
            # Only create a specialised position when somebody actually needs it, so a
            # company that never had an HR Admin does not gain an empty node.
            holders = [
                row[0]
                for row in bind.execute(
                    sa.text(
                        "SELECT id FROM users WHERE tenant_id = :tenant "
                        "AND role = :role AND department = :department"
                    ),
                    {
                        "tenant": tenant_id,
                        "role": legacy_role,
                        "department": legacy_department,
                    },
                ).all()
            ]
            if not holders and slug not in positions:
                continue

            position_id = positions.get(slug)
            if position_id is None:
                position_id = uuid.uuid4()
                bind.execute(
                    sa.text(
                        "INSERT INTO positions "
                        "(id, tenant_id, parent_id, name, slug, permissions, grants_all,"
                        " is_active, sort_order) "
                        "VALUES (:id, :tenant, :parent, :name, :slug,"
                        " CAST(:perms AS JSONB), false, true, :sort_order)"
                    ),
                    {
                        "id": position_id,
                        "tenant": tenant_id,
                        "parent": parent_id,
                        "name": name,
                        "slug": slug,
                        "perms": json.dumps(permissions),
                        "sort_order": 100 + order,
                    },
                )
                positions[slug] = position_id
            else:
                bind.execute(
                    sa.text(
                        "UPDATE positions SET permissions = CAST(:perms AS JSONB) "
                        "WHERE id = :id"
                    ),
                    {"perms": json.dumps(permissions), "id": position_id},
                )

            for holder_id in holders:
                bind.execute(
                    sa.text("UPDATE users SET position_id = :position WHERE id = :id"),
                    {"position": position_id, "id": holder_id},
                )


def downgrade() -> None:
    bind = op.get_bind()
    # Send anyone on a specialised position back to the generic one, then drop the nodes.
    for slug, _name, legacy_role, _department, _permissions in _SPECIALISATIONS:
        generic = "admin" if legacy_role == "Admin" else "manager"
        bind.execute(
            sa.text(
                "UPDATE users SET position_id = generic.id "
                "FROM positions specialised "
                "JOIN positions generic ON generic.tenant_id = specialised.tenant_id "
                "  AND generic.slug = :generic "
                "WHERE users.position_id = specialised.id AND specialised.slug = :slug"
            ),
            {"slug": slug, "generic": generic},
        )
        bind.execute(
            sa.text("DELETE FROM positions WHERE slug = :slug"), {"slug": slug}
        )
