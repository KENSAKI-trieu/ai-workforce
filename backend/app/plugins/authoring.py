"""Create, edit and delete the plugin packages a tenant writes for itself.

Packages come from two places and the difference matters throughout:

* **Built-in** -- YAML files under `backend/plugins/`, versioned with the code, reviewed
  like code, identical for every tenant. Read-only here; changing one is a code change.
* **Tenant-authored** -- rows in `tenant_plugins`, owned by one tenant, editable in the
  product without a release. Everything below is about these.

Both are validated by the same parser before they are ever used, so a package typed into
a text box cannot do anything a shipped one could not.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.models import TenantPlugin, TenantPluginInstall
from app.plugins.loader import parse_manifest_yaml, plugin_catalogue
from app.plugins.manifest import PluginManifest, PluginManifestError

# A manifest is prose plus a little structure; this is far above anything legitimate and
# far below anything that would strain the database or the parser.
MAX_MANIFEST_BYTES = 64_000

# One tenant cannot need more than this, and a cap keeps a runaway client from filling
# the table.
MAX_PLUGINS_PER_TENANT = 100


class PluginAuthoringError(ValueError):
    """Raised when a create, edit or delete cannot be carried out as asked."""


def list_tenant_plugins(db: Session, tenant_id: uuid.UUID) -> list[TenantPlugin]:
    return (
        db.query(TenantPlugin)
        .filter(TenantPlugin.tenant_id == tenant_id)
        .order_by(TenantPlugin.name)
        .all()
    )


def get_tenant_plugin(
    db: Session, tenant_id: uuid.UUID, name: str
) -> TenantPlugin | None:
    return (
        db.query(TenantPlugin)
        .filter(TenantPlugin.tenant_id == tenant_id, TenantPlugin.name == name)
        .first()
    )


def tenant_catalogue(db: Session, tenant_id: uuid.UUID) -> dict[str, PluginManifest]:
    """Validated manifests for a tenant's own packages, keyed by name.

    A row that no longer parses is skipped rather than raising. Validation happens on
    the way in, so this should not occur -- but a rule tightened in a later release can
    make a stored manifest invalid, and one such row must not take out every other
    package the tenant has enabled.
    """
    found: dict[str, PluginManifest] = {}
    for row in list_tenant_plugins(db, tenant_id):
        try:
            found[row.name] = parse_manifest_yaml(
                row.source_yaml, expected_name=row.name, source=f"tenant:{row.name}"
            )
        except PluginManifestError:
            continue
    return found


def available_manifests(db: Session, tenant_id: uuid.UUID) -> dict[str, PluginManifest]:
    """Every package this tenant may install: the built-in ones plus its own.

    Names cannot collide -- a tenant package is refused at write time if a built-in
    already answers to that name -- so the merge order here never decides anything.
    """
    return {**plugin_catalogue(), **tenant_catalogue(db, tenant_id)}


def _validate_submission(
    db: Session,
    tenant_id: uuid.UUID,
    source_yaml: str,
    *,
    expected_name: str | None,
) -> PluginManifest:
    if len(source_yaml.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise PluginAuthoringError(
            f"Manifest is larger than {MAX_MANIFEST_BYTES} bytes"
        )
    try:
        manifest = parse_manifest_yaml(source_yaml, expected_name=expected_name)
    except PluginManifestError as exc:
        raise PluginAuthoringError(str(exc)) from exc

    if manifest.name in plugin_catalogue():
        raise PluginAuthoringError(
            f"'{manifest.name}' is the name of a built-in package; choose another name"
        )
    return manifest


def create_tenant_plugin(
    db: Session,
    tenant_id: uuid.UUID,
    source_yaml: str,
    *,
    created_by: uuid.UUID | None = None,
) -> tuple[TenantPlugin, PluginManifest]:
    manifest = _validate_submission(db, tenant_id, source_yaml, expected_name=None)

    if get_tenant_plugin(db, tenant_id, manifest.name) is not None:
        raise PluginAuthoringError(
            f"A package named '{manifest.name}' already exists; edit it instead"
        )
    if len(list_tenant_plugins(db, tenant_id)) >= MAX_PLUGINS_PER_TENANT:
        raise PluginAuthoringError(
            f"This workspace already has {MAX_PLUGINS_PER_TENANT} packages"
        )

    row = TenantPlugin(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=manifest.name,
        version=manifest.version,
        display_name=manifest.display_name,
        target_role=manifest.target_role,
        source_yaml=source_yaml,
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, manifest


def update_tenant_plugin(
    db: Session, tenant_id: uuid.UUID, name: str, source_yaml: str
) -> tuple[TenantPlugin, PluginManifest]:
    """Rewrite a package in place.

    The name is fixed once created: an install row points at it by name, so letting an
    edit rename the package would leave that row pointing at nothing and silently drop
    the tenant back to default prompts.
    """
    row = get_tenant_plugin(db, tenant_id, name)
    if row is None:
        raise PluginAuthoringError(f"No package named '{name}' in this workspace")

    manifest = _validate_submission(db, tenant_id, source_yaml, expected_name=name)

    row.version = manifest.version
    row.display_name = manifest.display_name
    row.target_role = manifest.target_role
    row.source_yaml = source_yaml

    # An installed package that was just edited would otherwise be reported as drifted,
    # which is a disk-package concept: here there is only one copy and it is this one.
    install = (
        db.query(TenantPluginInstall)
        .filter(
            TenantPluginInstall.tenant_id == tenant_id,
            TenantPluginInstall.plugin_name == name,
        )
        .first()
    )
    if install is not None:
        install.plugin_version = manifest.version
        install.target_role = manifest.target_role

    db.commit()
    db.refresh(row)
    return row, manifest


def delete_tenant_plugin(db: Session, tenant_id: uuid.UUID, name: str) -> None:
    """Remove a package the tenant wrote.

    Refused while the package is still installed. Deleting an enabled package would
    change how the agent answers at the same moment, with nothing on screen connecting
    the two; uninstalling first makes that its own visible step.
    """
    row = get_tenant_plugin(db, tenant_id, name)
    if row is None:
        raise PluginAuthoringError(f"No package named '{name}' in this workspace")

    installed = (
        db.query(TenantPluginInstall)
        .filter(
            TenantPluginInstall.tenant_id == tenant_id,
            TenantPluginInstall.plugin_name == name,
        )
        .first()
    )
    if installed is not None:
        raise PluginAuthoringError(
            f"'{name}' is still installed; uninstall it before deleting"
        )

    db.delete(row)
    db.commit()
