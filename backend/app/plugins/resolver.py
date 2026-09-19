"""Resolve what the plugins installed for one tenant actually change.

Two things are resolved here, and they follow opposite rules on purpose:

* Prompts are *layered*. Each installed package may append to, or replace, the shipped
  text for a slot.
* Skills are *narrowed only*. A package can take tools away from an agent and can never
  add one. Effective access is the intersection with what the agent already had, and
  denials are a union. If a package could grant a tool, installing a package would
  become a privilege-escalation path and the security boundary would move from the
  permission layer into a YAML file written outside the engineering team.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Iterable, Mapping

from sqlalchemy.orm import Session

from app.models.models import TenantPluginInstall
from app.plugins.loader import plugin_catalogue
from app.plugins.manifest import PluginManifest
from app.services.agents.hr_prompts import default_prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillRestriction:
    """The narrowing a tenant's packages impose on one agent role.

    ``allowed`` is None when no installed package restricts the toolset, which is not the
    same as an empty set -- an empty set would mean every tool is withdrawn.
    """

    allowed: frozenset[str] | None = None
    denied: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        return self.allowed is None and not self.denied

    def permits(self, tool_name: str) -> bool:
        if tool_name in self.denied:
            return False
        return self.allowed is None or tool_name in self.allowed


EMPTY_RESTRICTION = SkillRestriction()


def installed_manifests(
    db: Session, tenant_id: uuid.UUID, role_code: str
) -> tuple[PluginManifest, ...]:
    """Validated manifests for a tenant's installed packages, in install order.

    Install order is stable and is what makes two packages that touch the same slot
    layer predictably. A row whose package has since been deleted from disk is skipped
    with a warning: the disk is the source of truth for behaviour, so a missing file
    means the tenant falls back to defaults rather than to stale text.
    """
    rows = (
        db.query(TenantPluginInstall)
        .filter(
            TenantPluginInstall.tenant_id == tenant_id,
            TenantPluginInstall.target_role == role_code.upper(),
        )
        .order_by(TenantPluginInstall.installed_at, TenantPluginInstall.plugin_name)
        .all()
    )
    if not rows:
        return ()

    catalogue = plugin_catalogue()
    manifests: list[PluginManifest] = []
    for row in rows:
        manifest = catalogue.get(row.plugin_name)
        if manifest is None:
            logger.warning(
                "Tenant %s has plugin '%s' installed but no package on disk provides it",
                tenant_id,
                row.plugin_name,
            )
            continue
        if manifest.version != row.plugin_version:
            # Not an error: local development edits packages in place constantly. It is
            # logged so a production surprise is traceable to the drift rather than to
            # the model.
            logger.info(
                "Plugin '%s' on disk is version %s, tenant %s installed %s",
                manifest.name,
                manifest.version,
                tenant_id,
                row.plugin_version,
            )
        manifests.append(manifest)
    return tuple(manifests)


def build_prompt_overlay(manifests: Iterable[PluginManifest]) -> dict[str, str]:
    """Fold prompt overrides onto the shipped defaults, in the order given."""
    overlay: dict[str, str] = {}
    for manifest in manifests:
        for override in manifest.prompts:
            base = overlay.get(override.slot) or default_prompt(override.slot)
            overlay[override.slot] = override.apply(base)
    return overlay


def build_skill_restriction(manifests: Iterable[PluginManifest]) -> SkillRestriction:
    """Combine the narrowing every installed package asks for.

    Two packages each restricting the toolset intersect: a tool survives only if every
    restricting package kept it. Denials accumulate and always win.
    """
    allowed: frozenset[str] | None = None
    denied: set[str] = set()
    for manifest in manifests:
        if manifest.tools_access is not None:
            granted = frozenset(manifest.tools_access)
            allowed = granted if allowed is None else (allowed & granted)
        denied.update(manifest.disallowed_actions)
    return SkillRestriction(allowed=allowed, denied=frozenset(denied))


def resolve_prompt_overlay(
    db: Session, tenant_id: uuid.UUID, role_code: str
) -> Mapping[str, str] | None:
    """Tenant-specific prompt text for an agent role, or None when nothing overrides it.

    Returning None rather than an empty mapping keeps the default path free of any
    string work for the overwhelming majority of tenants, which install nothing.
    """
    overlay = build_prompt_overlay(installed_manifests(db, tenant_id, role_code))
    return overlay or None


def resolve_skill_restriction(
    db: Session, tenant_id: uuid.UUID, role_code: str
) -> SkillRestriction:
    return build_skill_restriction(installed_manifests(db, tenant_id, role_code))


def effective_tools(
    agent_tools: Iterable[str], restriction: SkillRestriction
) -> frozenset[str]:
    """Apply a restriction to an agent's granted tools. Never returns a wider set."""
    tools = frozenset(agent_tools)
    if restriction.allowed is not None:
        tools &= restriction.allowed
    return tools - restriction.denied
