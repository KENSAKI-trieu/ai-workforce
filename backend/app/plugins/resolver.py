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

from app.models.models import AIAgent, TenantPluginInstall
from app.plugins.manifest import PluginManifest

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
    layer predictably. A row whose package no longer exists -- a built-in file removed
    in a release, or a tenant package deleted -- is skipped with a warning: the package
    is the source of truth, so a missing one drops the tenant back to the shipped
    prompt rather than to stale text the install row remembered.
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

    # Both sources, because an install row does not record which kind it points at.
    # Imported here rather than at module level: the authoring module reaches back into
    # the loader, and this module is on the loader's own import path.
    from app.plugins.authoring import available_manifests

    catalogue = available_manifests(db, tenant_id)
    manifests: list[PluginManifest] = []
    for row in rows:
        manifest = catalogue.get(row.plugin_name)
        if manifest is None:
            logger.warning(
                "Tenant %s has plugin '%s' installed but no package provides it",
                tenant_id,
                row.plugin_name,
            )
            continue
        if manifest.version != row.plugin_version:
            # Only built-in packages reach here: editing a tenant package updates its
            # install row in the same transaction. Not an error -- development edits
            # files in place constantly -- but logged so a production surprise is
            # traceable to the drift rather than to the model.
            logger.info(
                "Plugin '%s' provides version %s, tenant %s installed %s",
                manifest.name,
                manifest.version,
                tenant_id,
                row.plugin_version,
            )
        manifests.append(manifest)
    return tuple(manifests)


def build_prompt_overlay(manifests: Iterable[PluginManifest]) -> dict[str, str]:
    """Fold prompt overrides onto the shipped defaults, in the order given."""
    # Deferred for the same reason as in manifest.py: importing the agents package at
    # module level would close an import cycle back into this module.
    from app.services.agents.prompt_registry import default_prompt

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


def tenant_answer_overlay(db: Session, tenant_id: uuid.UUID, role_code: str) -> str:
    """The administrator's own free text for this agent, or an empty string.

    Read straight from `ai_agents.prompt_overlay`. Blank and whitespace-only are the
    same as unset, so an operator who clears the box is back on the shipped prompt
    exactly, not on a prompt with a stray blank line appended.
    """
    value = (
        db.query(AIAgent.prompt_overlay)
        .filter(
            AIAgent.tenant_id == tenant_id,
            AIAgent.role_code == role_code.upper(),
        )
        .scalar()
    )
    return (value or "").strip()


def resolve_prompt_overlay(
    db: Session, tenant_id: uuid.UUID, role_code: str
) -> Mapping[str, str] | None:
    """Tenant-specific prompt text for an agent role, or None when nothing overrides it.

    Two sources are layered, packages first and the administrator's own text last, so a
    person editing the text box can see their wording win over what a package said. The
    text box only ever reaches the slot that writes this role's replies; see the column's
    comment for why. It used to be HR's `answer` slot whatever the role, so text entered
    for the Legal agent landed in a slot the Legal flow never reads and changed nothing.

    Returning None rather than an empty mapping keeps the default path free of any
    string work for the overwhelming majority of tenants, which customise nothing.
    """
    from app.services.agents.prompt_registry import answer_slot_for_role, default_prompt

    overlay = build_prompt_overlay(installed_manifests(db, tenant_id, role_code))

    own_text = tenant_answer_overlay(db, tenant_id, role_code)
    answer_slot = answer_slot_for_role(role_code)
    if own_text and answer_slot:
        base = overlay.get(answer_slot) or default_prompt(answer_slot)
        overlay[answer_slot] = f"{base}\n\n{own_text}"

    return overlay or None


def tenant_graph_instructions(db: Session, tenant_id: uuid.UUID, role_code: str) -> str:
    """A tenant's conventions for an agent that runs through LangGraph.

    The graph has its own prompts, so the shipped text of a slot means nothing there;
    what carries over is what the tenant *added*: every package's `append` text for the
    role's answer slot, then the administrator's own text, in the order the deterministic
    flow layers them. A `replace` override is left out -- it rewrites a prompt written for
    the deterministic flow, output format included, which the graph does not use.
    The graph places this after its own rules, so it can change tone and terminology but
    not what the agent is allowed to do.
    """
    from app.services.agents.prompt_registry import answer_slot_for_role

    answer_slot = answer_slot_for_role(role_code)
    parts: list[str] = []
    if answer_slot:
        parts = [
            override.text.strip()
            for manifest in installed_manifests(db, tenant_id, role_code)
            for override in manifest.prompts
            if override.slot == answer_slot and override.mode == "append" and override.text.strip()
        ]
    own_text = tenant_answer_overlay(db, tenant_id, role_code)
    if own_text:
        parts.append(own_text)
    return "\n\n".join(parts)


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
