"""Parse and validate a plugin package manifest.

A manifest is untrusted input: packages are authored per customer and may be written by
somebody outside the engineering team. Everything here therefore fails closed -- an
unknown key, an unknown prompt slot, an unknown tool name or a malformed version is a
rejection, not a warning -- so a typo cannot quietly disable part of a package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class PluginManifestError(ValueError):
    """Raised when a manifest is malformed, or asks for something that cannot exist."""


NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

PROMPT_MODES = frozenset({"append", "replace"})


def prompt_slots_for_role(role_code: str) -> tuple[str, ...] | None:
    """Slot names a role accepts overrides for, or None when it accepts none.

    The import is deferred because `app.services.agents` eagerly loads the agent
    executor, which loads the plugin resolver, which loads this module. Reaching for
    the slot names only when a manifest is actually parsed keeps this module a leaf and
    that cycle unformed.

    A role absent from the map cannot carry prompt overrides at all, which is
    deliberate: only a flow that reads a resolved overlay can honour one, so accepting
    prompts for another role would store text nothing ever applies.
    """
    from app.services.agents.prompt_registry import prompt_slots_for_role as slots

    return slots(role_code)


MANIFEST_KEYS = frozenset({
    "name",
    "version",
    "display_name",
    "description",
    "target_role",
    "min_platform_version",
    "prompts",
    "skills",
    "knowledge",
})

SKILL_KEYS = frozenset({"tools_access", "disallowed_actions"})

# Bumped when the manifest contract changes in a way older packages cannot satisfy.
PLATFORM_VERSION = 1


@dataclass(frozen=True)
class PromptOverride:
    slot: str
    mode: str
    text: str

    def apply(self, base: str) -> str:
        if self.mode == "replace":
            return self.text
        return f"{base}\n\n{self.text}"


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    display_name: str
    target_role: str
    description: str = ""
    min_platform_version: int = 1
    prompts: tuple[PromptOverride, ...] = ()
    tools_access: tuple[str, ...] | None = None
    disallowed_actions: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    source_path: str = ""

    def public_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "target_role": self.target_role,
            "prompt_slots": [override.slot for override in self.prompts],
            "prompt_modes": {override.slot: override.mode for override in self.prompts},
            "restricts_tools": self.tools_access is not None,
            "tools_access": list(self.tools_access or []),
            "disallowed_actions": list(self.disallowed_actions),
            "knowledge": list(self.knowledge),
        }


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PluginManifestError(f"{where} must be a mapping")
    return value


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PluginManifestError(f"{where} must be a list of strings")
    cleaned = tuple(sorted({item.strip() for item in value if item.strip()}))
    return cleaned


def _parse_prompts(raw: Any, target_role: str) -> tuple[PromptOverride, ...]:
    prompts = _require_mapping(raw, "prompts")
    known_slots = prompt_slots_for_role(target_role)
    if known_slots is None:
        raise PluginManifestError(
            f"Role '{target_role}' does not support prompt overrides yet"
        )

    overrides: list[PromptOverride] = []
    for slot, body in prompts.items():
        if slot not in known_slots:
            raise PluginManifestError(
                f"Unknown prompt slot '{slot}' for role {target_role}; "
                f"expected one of {', '.join(known_slots)}"
            )
        # A bare string is the common case and means the safe mode.
        if isinstance(body, str):
            mode, text = "append", body
        else:
            body = _require_mapping(body, f"prompts.{slot}")
            unknown = set(body) - {"mode", "text"}
            if unknown:
                raise PluginManifestError(
                    f"Unknown key(s) in prompts.{slot}: {', '.join(sorted(unknown))}"
                )
            mode = str(body.get("mode") or "append")
            text = body.get("text")
        if mode not in PROMPT_MODES:
            raise PluginManifestError(
                f"prompts.{slot}.mode must be 'append' or 'replace', got '{mode}'"
            )
        if not isinstance(text, str) or not text.strip():
            raise PluginManifestError(f"prompts.{slot}.text must be a non-empty string")
        overrides.append(PromptOverride(slot=slot, mode=mode, text=text.strip()))

    # Deterministic order keeps two installs of the same package byte-identical.
    return tuple(sorted(overrides, key=lambda override: override.slot))


def _parse_skills(raw: Any, known_tools: frozenset[str]) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    skills = _require_mapping(raw, "skills")
    unknown = set(skills) - SKILL_KEYS
    if unknown:
        raise PluginManifestError(
            f"Unknown key(s) in skills: {', '.join(sorted(unknown))}"
        )

    # Imported here: app.core is a leaf, but keeping the parser's import surface small
    # matters to the CLI that loads it.
    from app.core.tool_permissions import canonical_tool_names

    tools: tuple[str, ...] | None = None
    if "tools_access" in skills:
        # Old names are accepted and stored in their current spelling, so a package
        # written before the rename still installs and still narrows the right tool.
        tools = tuple(canonical_tool_names(_string_list(skills["tools_access"], "skills.tools_access")))
    denied = tuple(canonical_tool_names(
        _string_list(skills.get("disallowed_actions", []), "skills.disallowed_actions")
    ))

    known = set(canonical_tool_names(known_tools))
    for name in tuple(tools or ()) + denied:
        if name not in known:
            raise PluginManifestError(f"Unknown tool name: {name}")

    overlap = set(tools or ()) & set(denied)
    if overlap:
        raise PluginManifestError(
            "A tool cannot be both granted and denied by one plugin: "
            + ", ".join(sorted(overlap))
        )
    return tools, denied


def parse_manifest(
    data: Any,
    *,
    known_tools: frozenset[str],
    known_roles: frozenset[str],
    source_path: str = "",
) -> PluginManifest:
    """Turn raw manifest data into a validated manifest, or raise PluginManifestError."""
    data = _require_mapping(data, "manifest")
    unknown = set(data) - MANIFEST_KEYS
    if unknown:
        raise PluginManifestError(
            f"Unknown top-level key(s): {', '.join(sorted(unknown))}"
        )

    name = str(data.get("name") or "").strip()
    if not NAME_PATTERN.match(name):
        raise PluginManifestError(
            f"name must be lowercase letters, digits and hyphens: got '{name}'"
        )

    version = str(data.get("version") or "").strip()
    if not VERSION_PATTERN.match(version):
        raise PluginManifestError(f"version must look like 1.0.0: got '{version}'")

    target_role = str(data.get("target_role") or "").strip().upper()
    if target_role not in known_roles:
        raise PluginManifestError(f"Unknown target_role: '{target_role}'")

    min_platform_version = data.get("min_platform_version", 1)
    if not isinstance(min_platform_version, int) or min_platform_version < 1:
        raise PluginManifestError("min_platform_version must be a positive integer")
    if min_platform_version > PLATFORM_VERSION:
        raise PluginManifestError(
            f"Plugin needs platform version {min_platform_version}, "
            f"this build provides {PLATFORM_VERSION}"
        )

    prompts = _parse_prompts(data["prompts"], target_role) if "prompts" in data else ()
    tools, denied = (
        _parse_skills(data["skills"], known_tools) if "skills" in data else (None, ())
    )
    knowledge = _string_list(data.get("knowledge", []), "knowledge")

    if not prompts and tools is None and not denied and not knowledge:
        raise PluginManifestError("Plugin does nothing: declare prompts, skills or knowledge")

    return PluginManifest(
        name=name,
        version=version,
        display_name=str(data.get("display_name") or name).strip(),
        description=str(data.get("description") or "").strip(),
        target_role=target_role,
        min_platform_version=min_platform_version,
        prompts=prompts,
        tools_access=tools,
        disallowed_actions=denied,
        knowledge=knowledge,
        source_path=source_path,
    )
