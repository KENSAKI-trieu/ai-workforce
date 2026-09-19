"""Tenant-scoped plugin catalogue and install management."""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import RoleRequired, get_current_active_user
from app.models.models import User
from app.plugins.loader import plugin_catalogue
from app.plugins.resolver import installed_manifests, build_prompt_overlay
from app.plugins.service import (
    PluginInstallError,
    install_plugin,
    list_installs,
    uninstall_plugin,
)
from app.services.agents.hr_prompts import default_prompt
from app.services.audit_service import log_audit_action

router = APIRouter(prefix="/plugins", tags=["Plugins"])

# Same set that already guards AI Employee configuration. Installing a package changes
# how an agent answers for the whole tenant, so it belongs behind the same door.
PLUGIN_ADMIN_ROLES = ("Owner", "Admin", "CEO")


class InstalledPluginResponse(BaseModel):
    plugin_name: str
    plugin_version: str
    target_role: str
    installed_at: str
    # Set when the package on disk has moved on from the version this tenant installed.
    disk_version: str | None = None
    drifted: bool = False
    missing_on_disk: bool = False


@router.get("/", summary="List available plugin packages")
def list_catalogue(
    current_user: User = Depends(get_current_active_user),
) -> List[dict[str, Any]]:
    return [manifest.public_metadata() for manifest in plugin_catalogue().values()]


@router.get("/installed", summary="List plugins installed for this tenant")
def list_installed(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> List[InstalledPluginResponse]:
    catalogue = plugin_catalogue()
    results: List[InstalledPluginResponse] = []
    for row in list_installs(db, current_user.tenant_id):
        manifest = catalogue.get(row.plugin_name)
        results.append(
            InstalledPluginResponse(
                plugin_name=row.plugin_name,
                plugin_version=row.plugin_version,
                target_role=row.target_role,
                installed_at=row.installed_at.isoformat() if row.installed_at else "",
                disk_version=manifest.version if manifest else None,
                drifted=bool(manifest and manifest.version != row.plugin_version),
                missing_on_disk=manifest is None,
            )
        )
    return results


@router.get("/preview/{role_code}/{slot}", summary="Preview the resolved prompt for a slot")
def preview_prompt(
    role_code: str,
    slot: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(RoleRequired(*PLUGIN_ADMIN_ROLES)),
) -> dict[str, Any]:
    """Show exactly the text the model will receive, defaults included.

    Reviewing a package by reading its YAML is not the same as seeing the result: an
    `append` override is only meaningful next to the prompt it is appended to.
    """
    try:
        base = default_prompt(slot)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    manifests = installed_manifests(db, current_user.tenant_id, role_code)
    overlay = build_prompt_overlay(manifests)
    resolved = overlay.get(slot) or base
    return {
        "role_code": role_code.upper(),
        "slot": slot,
        "is_overridden": slot in overlay,
        "default_prompt": base,
        "resolved_prompt": resolved,
        "contributing_plugins": [
            manifest.name
            for manifest in manifests
            if any(override.slot == slot for override in manifest.prompts)
        ],
    }


@router.post(
    "/{plugin_name}/install",
    summary="Install a plugin package for this tenant",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def install(
    plugin_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    try:
        row, manifest = install_plugin(
            db, current_user.tenant_id, plugin_name, installed_by=current_user.id
        )
    except PluginInstallError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    log_audit_action(
        db,
        current_user.tenant_id,
        manifest.target_role,
        "plugin.install",
        {"plugin_name": manifest.name, "plugin_version": manifest.version},
        {"installed_by": str(current_user.id)},
    )
    return {
        "plugin_name": row.plugin_name,
        "plugin_version": row.plugin_version,
        "target_role": row.target_role,
        "manifest": manifest.public_metadata(),
    }


@router.delete(
    "/{plugin_name}",
    summary="Uninstall a plugin package from this tenant",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def uninstall(
    plugin_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    try:
        target_role = uninstall_plugin(db, current_user.tenant_id, plugin_name)
    except PluginInstallError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    log_audit_action(
        db,
        current_user.tenant_id,
        target_role,
        "plugin.uninstall",
        {"plugin_name": plugin_name},
        {"uninstalled_by": str(current_user.id)},
    )
    return {"plugin_name": plugin_name, "target_role": target_role, "status": "uninstalled"}
