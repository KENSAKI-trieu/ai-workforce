"""Lifecycle management for durable LangGraph checkpoint storage."""

from __future__ import annotations

import os
import threading
from contextlib import AbstractContextManager
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings, settings
from app.orchestration.engine import LangGraphEngine


os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")


class OrchestrationEngineProvider:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._postgres_context: AbstractContextManager[Any] | None = None
        self._engine: LangGraphEngine | None = None

    @property
    def backend(self) -> str:
        return self.config.LANGGRAPH_CHECKPOINT_BACKEND.lower()

    def start(self) -> LangGraphEngine:
        with self._lock:
            if self._engine is not None:
                return self._engine
            if self.backend == "memory":
                if self.config.APP_ENV.lower() in {"production", "staging"} and self.config.LANGGRAPH_ENABLED:
                    raise RuntimeError("PostgreSQL checkpointer is required outside development/test")
                self._engine = LangGraphEngine(checkpointer=InMemorySaver())
                return self._engine
            if self.backend != "postgres":
                raise ValueError(f"Unsupported LangGraph checkpoint backend: {self.backend}")

            database_url = self.config.langgraph_checkpoint_database_url
            if not database_url:
                raise RuntimeError("LANGGRAPH_CHECKPOINT_DATABASE_URL or PostgreSQL settings are required")
            from langgraph.checkpoint.postgres import PostgresSaver

            context = PostgresSaver.from_conn_string(database_url)
            checkpointer = context.__enter__()
            try:
                if self.config.LANGGRAPH_CHECKPOINT_AUTO_SETUP:
                    checkpointer.setup()
                self._engine = LangGraphEngine(checkpointer=checkpointer)
                self._postgres_context = context
            except Exception:
                context.__exit__(None, None, None)
                raise
            return self._engine

    def get(self) -> LangGraphEngine:
        return self.start()

    def close(self) -> None:
        with self._lock:
            context = self._postgres_context
            self._postgres_context = None
            self._engine = None
        if context is not None:
            context.__exit__(None, None, None)
