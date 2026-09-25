"""Discover plugin packages on disk and hand back validated manifests.

Packages live in ``backend/plugins/<name>/plugin.yaml`` rather than at the repository
root, because the backend image is built with ``./backend`` as its context -- a package
outside that directory would simply not exist in the container.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml

from app.plugins.manifest import PluginManifest, PluginManifestError, parse_manifest

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "plugin.yaml"

# backend/app/plugins/loader.py -> backend/plugins
DEFAULT_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins"


def plugin_root() -> Path:
    """Where packages are read from; overridable so tests can use a temp directory."""
    override = os.getenv("PLUGIN_ROOT")
    return Path(override).resolve() if override else DEFAULT_PLUGIN_ROOT


def _known_tools_and_roles() -> tuple[frozenset[str], frozenset[str]]:
    # Imported lazily: the tool catalogue pulls in the service layer, and the manifest
    # parser is also used from the CLI, where paying for that import at module load is
    # pure cost.
    from app.core.gateway_tools import GATEWAY_TOOLS
    from app.core.hr_capabilities import HR_CORE_TOOLS
    from app.domains.platform.auth_service import DEFAULT_AGENT_TOOLS

    tools: set[str] = set(GATEWAY_TOOLS) | set(HR_CORE_TOOLS)
    for granted in DEFAULT_AGENT_TOOLS.values():
        tools.update(granted)
    return frozenset(tools), frozenset(DEFAULT_AGENT_TOOLS)


def parse_manifest_yaml(
    text: str, *, expected_name: str | None = None, source: str = ""
) -> PluginManifest:
    """Validate a manifest written as YAML text, wherever it came from.

    Disk packages and tenant-authored ones go through this one function, so a package
    typed into the product is held to exactly the same rules as one shipped in the
    repository -- same unknown-key rejection, same tool-name check, same slot check.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PluginManifestError(f"Not valid YAML: {exc}") from exc

    known_tools, known_roles = _known_tools_and_roles()
    manifest = parse_manifest(
        raw,
        known_tools=known_tools,
        known_roles=known_roles,
        source_path=source,
    )
    if expected_name is not None and manifest.name != expected_name:
        raise PluginManifestError(
            f"Plugin name '{manifest.name}' does not match '{expected_name}'"
        )
    return manifest


def load_manifest_file(path: Path) -> PluginManifest:
    """Read and validate one manifest file. Raises PluginManifestError on bad input."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginManifestError(f"Cannot read {path}: {exc}") from exc
    return parse_manifest_yaml(
        text, expected_name=path.parent.name, source=str(path)
    )


def discover_plugins(root: Path | None = None) -> dict[str, PluginManifest]:
    """Return every valid package found, keyed by name.

    A package that fails validation is logged and skipped rather than taking the whole
    catalogue down with it: one customer's broken file must not stop every other tenant
    from resolving the packages they already installed.
    """
    base = root or plugin_root()
    found: dict[str, PluginManifest] = {}
    if not base.is_dir():
        return found

    for manifest_path in sorted(base.glob(f"*/{MANIFEST_FILENAME}")):
        try:
            manifest = load_manifest_file(manifest_path)
        except PluginManifestError:
            logger.warning("Skipping invalid plugin at %s", manifest_path, exc_info=True)
            continue
        found[manifest.name] = manifest
    return found


@lru_cache(maxsize=1)
def _cached_catalogue(root_key: str) -> dict[str, PluginManifest]:
    return discover_plugins(Path(root_key))


def plugin_catalogue(*, refresh: bool = False) -> dict[str, PluginManifest]:
    """The discovered packages, cached per process. Manifests are frozen dataclasses."""
    if refresh:
        _cached_catalogue.cache_clear()
    return _cached_catalogue(str(plugin_root()))


def get_plugin(name: str) -> PluginManifest | None:
    return plugin_catalogue().get(name)
