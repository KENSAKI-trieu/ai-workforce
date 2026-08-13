from __future__ import annotations

import sys
import types

import pytest

from app.config import Settings
from app.orchestration.persistence import OrchestrationEngineProvider


def test_memory_checkpointer_is_rejected_for_enabled_production_graph() -> None:
    provider = OrchestrationEngineProvider(Settings(
        APP_ENV="production",
        LANGGRAPH_ENABLED=True,
        LANGGRAPH_CHECKPOINT_BACKEND="memory",
    ))
    with pytest.raises(RuntimeError, match="PostgreSQL checkpointer"):
        provider.start()


def test_postgres_checkpoint_url_is_derived_and_escaped() -> None:
    config = Settings(
        LANGGRAPH_CHECKPOINT_BACKEND="postgres",
        POSTGRES_HOST="postgres",
        POSTGRES_DB="ai workforce",
        POSTGRES_USER="graph user",
        POSTGRES_PASSWORD="p@ss word",
    )
    assert config.langgraph_checkpoint_database_url == (
        "postgresql://graph+user:p%40ss+word@postgres:5432/ai+workforce"
    )


def test_postgres_provider_runs_setup_and_closes_context(monkeypatch) -> None:
    events: list[str] = []

    class FakeCheckpointer:
        def setup(self) -> None:
            events.append("setup")

    class FakeContext:
        def __enter__(self):
            events.append("enter")
            return FakeCheckpointer()

        def __exit__(self, *args):
            events.append("exit")

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, value: str):
            assert value == "postgresql://graph:test@postgres:5432/graph"
            return FakeContext()

    fake_module = types.ModuleType("langgraph.checkpoint.postgres")
    fake_module.PostgresSaver = FakeSaver
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", fake_module)
    monkeypatch.setattr(
        "app.orchestration.persistence.LangGraphEngine",
        lambda *, checkpointer: ("engine", checkpointer),
    )
    provider = OrchestrationEngineProvider(Settings(
        LANGGRAPH_CHECKPOINT_BACKEND="postgres",
        LANGGRAPH_CHECKPOINT_DATABASE_URL="postgresql://graph:test@postgres:5432/graph",
    ))
    assert provider.get()[0] == "engine"
    assert events == ["enter", "setup"]
    provider.close()
    assert events == ["enter", "setup", "exit"]
