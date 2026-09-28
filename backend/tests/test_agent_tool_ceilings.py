"""Every tool an ai-service agent may be offered is one the backend actually defines.

The ai-service names each agent's tools as plain strings, and its graph drops any name the
backend's contracts do not return. A misspelled or renamed tool therefore did not fail
anything: the agent simply ran without it. The two services share the `app` package name,
so the ceilings are read from source rather than imported.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.tools.registry import tool_registry

AGENTS_DIR = Path(__file__).resolve().parents[2] / "apps" / "ai-service" / "app" / "agents"


def _ceiling(path: Path) -> tuple[str, ...]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "TOOLS":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "TOOLS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path} defines no TOOLS")


def _ceiling_files() -> list[Path]:
    files = sorted(AGENTS_DIR.glob("*/tools.py"))
    assert files, f"No agent tool ceilings found under {AGENTS_DIR}"
    return files


@pytest.mark.parametrize("path", _ceiling_files(), ids=lambda path: path.parent.name)
def test_agent_tool_ceiling_names_backend_tools(path: Path) -> None:
    defined = {definition.name for definition in tool_registry.all()}
    unknown = sorted(set(_ceiling(path)) - defined)
    assert not unknown, f"{path.parent.name} names tools the backend does not define: {unknown}"
