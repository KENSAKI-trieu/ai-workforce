"""Tenant-scoped plugin catalogue and install management."""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import RoleRequired, get_current_active_user
from app.models.models import User
from app.plugins.authoring import (
    MAX_MANIFEST_BYTES,
    PluginAuthoringError,
    available_manifests,
    create_tenant_plugin,
    delete_tenant_plugin,
    get_tenant_plugin,
    list_tenant_plugins,
    update_tenant_plugin,
)
from app.plugins.loader import parse_manifest_yaml, plugin_catalogue
from app.plugins.manifest import PluginManifestError
from app.plugins.resolver import (
    installed_manifests,
    resolve_prompt_overlay,
    tenant_answer_overlay,
    tenant_graph_instructions,
)
from app.plugins.service import (
    PluginInstallError,
    install_plugin,
    list_installs,
    uninstall_plugin,
)
from app.agents.prompt_registry import answer_slot_for_role, default_prompt, prompt_slots_for_role
from app.core.agent_engines import uses_langgraph
from app.domains.platform.audit_service import log_audit_action

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


class PluginSourceRequest(BaseModel):
    source_yaml: str = Field(min_length=1, max_length=MAX_MANIFEST_BYTES)


@router.get("/", summary="List available plugin packages")
def list_catalogue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> List[dict[str, Any]]:
    """Built-in packages and this workspace's own, in one list.

    `editable` is what the UI needs to know: a built-in package is versioned with the
    code and cannot be changed here, while a package this workspace wrote can be.
    """
    built_in = set(plugin_catalogue())
    return [
        {**manifest.public_metadata(), "editable": name not in built_in}
        for name, manifest in available_manifests(db, current_user.tenant_id).items()
    ]


@router.post(
    "/validate",
    summary="Check a manifest without saving it",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def validate_source(req: PluginSourceRequest) -> dict[str, Any]:
    """Report what a manifest would do, or why it is rejected.

    Separate from saving so an author can correct a typo without creating, deleting and
    recreating a package to find out what was wrong.
    """
    try:
        manifest = parse_manifest_yaml(req.source_yaml)
    except PluginManifestError as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True, "manifest": manifest.public_metadata()}


@router.get(
    "/authored",
    summary="List the packages this workspace wrote",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def list_authored(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> List[dict[str, Any]]:
    return [
        {
            "name": row.name,
            "version": row.version,
            "display_name": row.display_name,
            "target_role": row.target_role,
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }
        for row in list_tenant_plugins(db, current_user.tenant_id)
    ]


@router.get(
    "/authored/{plugin_name}",
    summary="Read one authored package for editing",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def read_authored(
    plugin_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    row = get_tenant_plugin(db, current_user.tenant_id, plugin_name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No package named '{plugin_name}'")
    return {
        "name": row.name,
        "version": row.version,
        "display_name": row.display_name,
        "target_role": row.target_role,
        "source_yaml": row.source_yaml,
    }


@router.post(
    "/authored",
    summary="Create a package for this workspace",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def create_authored(
    req: PluginSourceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    try:
        row, manifest = create_tenant_plugin(
            db, current_user.tenant_id, req.source_yaml, created_by=current_user.id
        )
    except PluginAuthoringError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    log_audit_action(
        db,
        current_user.tenant_id,
        manifest.target_role,
        "plugin.authored.create",
        {"plugin_name": manifest.name, "plugin_version": manifest.version},
        {"created_by": str(current_user.id)},
    )
    return {"name": row.name, "manifest": manifest.public_metadata()}


@router.put(
    "/authored/{plugin_name}",
    summary="Rewrite a package this workspace wrote",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def update_authored(
    plugin_name: str,
    req: PluginSourceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    try:
        row, manifest = update_tenant_plugin(
            db, current_user.tenant_id, plugin_name, req.source_yaml
        )
    except PluginAuthoringError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    log_audit_action(
        db,
        current_user.tenant_id,
        manifest.target_role,
        "plugin.authored.update",
        {"plugin_name": manifest.name, "plugin_version": manifest.version},
        {"updated_by": str(current_user.id)},
    )
    return {"name": row.name, "manifest": manifest.public_metadata()}


@router.delete(
    "/authored/{plugin_name}",
    summary="Delete a package this workspace wrote",
    dependencies=[Depends(RoleRequired(*PLUGIN_ADMIN_ROLES))],
)
def delete_authored(
    plugin_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    try:
        delete_tenant_plugin(db, current_user.tenant_id, plugin_name)
    except PluginAuthoringError as exc:
        # 409 rather than 422 for the still-installed case: the manifest is fine, the
        # workspace state is what refuses.
        status = 409 if "still installed" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    log_audit_action(
        db,
        current_user.tenant_id,
        "",
        "plugin.authored.delete",
        {"plugin_name": plugin_name},
        {"deleted_by": str(current_user.id)},
    )
    return {"name": plugin_name, "status": "deleted"}


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

    A role that runs through LangGraph does not read these slots on a normal turn -- the
    graph has its own prompts and takes only what the tenant *added* to the reply slot.
    The slots still matter there, because a turn falls back to the deterministic flow
    when the graph's model is unavailable, so both are returned and `graph` says which
    one a normal turn uses. Showing the deterministic prompt alone, as this did, told an
    operator a `replace` override was live when the graph was ignoring it.
    """
    role = role_code.strip().upper()
    if slot not in (prompt_slots_for_role(role) or ()):
        # Checked against the role, not just the slot name: `/preview/HR/legal_answer`
        # used to resolve HR's overlay and show Legal's slot as never customised.
        raise HTTPException(status_code=404, detail=f"Unknown prompt slot for {role}: {slot}")
    base = default_prompt(slot)

    # Resolved through exactly the path the chat turn uses, packages and the tenant's
    # own text together. Rebuilding only the package half here would make this panel
    # disagree with what the model is actually sent, which is the failure it exists to
    # prevent.
    overlay = resolve_prompt_overlay(db, current_user.tenant_id, role) or {}
    resolved = overlay.get(slot) or base
    manifests = installed_manifests(db, current_user.tenant_id, role)
    contributing = [
        manifest for manifest in manifests
        if any(override.slot == slot for override in manifest.prompts)
    ]
    answer_slot = answer_slot_for_role(role)
    is_answer_slot = slot == answer_slot

    graph: dict[str, Any] | None = None
    if uses_langgraph(role):
        graph = {
            # Only the reply slot reaches the graph, and only through its appends.
            "slot_applies": is_answer_slot,
            "instructions": (
                tenant_graph_instructions(db, current_user.tenant_id, role)
                if is_answer_slot else ""
            ),
            # Packages customising this slot whose text the graph never sees.
            "ignored_plugins": [
                manifest.name
                for manifest in contributing
                if not is_answer_slot or any(
                    override.slot == slot and override.mode != "append"
                    for override in manifest.prompts
                )
            ],
        }

    return {
        "role_code": role,
        "slot": slot,
        "engine": "langgraph" if graph is not None else "deterministic",
        "is_overridden": resolved != base,
        "default_prompt": base,
        "resolved_prompt": resolved,
        "contributing_plugins": [manifest.name for manifest in contributing],
        # The administrator's text box lands on the role's reply slot, whose name differs
        # per role; this compared against HR's "answer" and was always false for Legal.
        "has_tenant_text": bool(
            is_answer_slot and tenant_answer_overlay(db, current_user.tenant_id, role)
        ),
        "graph": graph,
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
