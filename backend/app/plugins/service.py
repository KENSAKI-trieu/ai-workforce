"""Install and uninstall plugin packages for a tenant.

Kept apart from the HTTP layer so the CLI and the API share exactly one implementation
of what installing means -- including its validation, which is the part worth having
only once.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.models import TenantPluginInstall
from app.plugins.manifest import PluginManifest


class PluginInstallError(ValueError):
    """Raised when an install or uninstall cannot be carried out as asked."""


def list_installs(db: Session, tenant_id: uuid.UUID) -> list[TenantPluginInstall]:
    return (
        db.query(TenantPluginInstall)
        .filter(TenantPluginInstall.tenant_id == tenant_id)
        .order_by(TenantPluginInstall.installed_at, TenantPluginInstall.plugin_name)
        .all()
    )


def get_install(
    db: Session, tenant_id: uuid.UUID, plugin_name: str
) -> TenantPluginInstall | None:
    return (
        db.query(TenantPluginInstall)
        .filter(
            TenantPluginInstall.tenant_id == tenant_id,
            TenantPluginInstall.plugin_name == plugin_name,
        )
        .first()
    )


def install_plugin(
    db: Session,
    tenant_id: uuid.UUID,
    plugin_name: str,
    *,
    installed_by: uuid.UUID | None = None,
) -> tuple[TenantPluginInstall, PluginManifest]:
    """Enable a package for a tenant, or re-point an existing install at a new version."""
    from app.plugins.authoring import available_manifests

    manifest = available_manifests(db, tenant_id).get(plugin_name)
    if manifest is None:
        raise PluginInstallError(f"No plugin package named '{plugin_name}' was found")

    existing = get_install(db, tenant_id, plugin_name)
    if existing is not None:
        # Re-installing is how a tenant picks up an edited package, so this updates the
        # recorded version instead of refusing.
        existing.plugin_version = manifest.version
        existing.target_role = manifest.target_role
        existing.installed_by = installed_by
        db.commit()
        db.refresh(existing)
        return existing, manifest

    row = TenantPluginInstall(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        plugin_name=manifest.name,
        plugin_version=manifest.version,
        target_role=manifest.target_role,
        installed_by=installed_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, manifest


def uninstall_plugin(db: Session, tenant_id: uuid.UUID, plugin_name: str) -> str:
    """Disable a package for a tenant. Returns the role the package targeted."""
    existing = get_install(db, tenant_id, plugin_name)
    if existing is None:
        raise PluginInstallError(f"Plugin '{plugin_name}' is not installed for this tenant")
    target_role = existing.target_role
    db.delete(existing)
    db.commit()
    return target_role
