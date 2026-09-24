"""Patch a name everywhere the chat modules look it up.

All chat flows used to live in one module, agent_executor.py, so a single
``monkeypatch.setattr(agent_executor, name, value)`` reached every place a name was used.
Since the split into app/agents/*, one helper is imported into several modules -- the HR
router, for example, is read by both hr/stream.py and hr/flow.py -- and patching one of
them would leave the other on the real function.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import pytest

CHAT_MODULES = (
    "app.agents.chat",
    "app.agents.access",
    "app.agents.usage",
    "app.agents.text",
    "app.agents.hr.stream",
    "app.agents.hr.flow",
    "app.agents.hr.intent",
    "app.agents.hr.leave",
    "app.agents.hr.profile",
    "app.agents.legal.flow",
    "app.agents.legal.intent",
    "app.agents.legal.review",
    "app.agents.knowledge.flow",
)


def patch_chat(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    for module_name in CHAT_MODULES:
        importlib.import_module(module_name)
    patched = False
    for module_name, module in list(sys.modules.items()):
        if module_name.startswith("app.agents") and hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    assert patched, f"no chat module uses {name}"
