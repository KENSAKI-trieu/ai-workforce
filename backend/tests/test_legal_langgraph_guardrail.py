"""Guards against LANGGRAPH_ENABLED silently changing how Legal reviews contracts.

Turning the flag on makes `_execute_agent_chat_core` return through LangGraph before
the LEGAL branch is ever reached. Contract review then runs through the
`audit_contract_risk` gateway tool instead of that branch; these tests pin the flag's
default, the fallback to the deterministic reviewer, and the startup notice.
"""

import logging

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from tests.chat_patching import patch_chat
from app.clients.ai_service_client import AIServiceError


def test_langgraph_flag_defaults_off():
    """The single assertion that catches an accidental default flip in config.py."""
    assert settings.LANGGRAPH_ENABLED is False


def test_langgraph_failure_falls_back_to_contract_review(
    client, employee_token_headers, monkeypatch
):
    """A 5xx from the graph must fall back to the deterministic reviewer, not 500."""

    calls: list[str] = []

    class FailingEngine:
        def execute(self, **kwargs):
            calls.append("execute")
            raise AIServiceError("gateway down", status_code=503)

    monkeypatch.setattr(settings, "LANGGRAPH_ENABLED", True)
    monkeypatch.setattr(settings, "LANGGRAPH_LEGACY_FALLBACK", True)
    patch_chat(monkeypatch, "LangGraphEngine", FailingEngine)

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "agent_role": "LEGAL",
            "message": "Rà soát hợp đồng dịch vụ: mức phạt vi phạm 30% và Bên A "
                       "có quyền đơn phương chấm dứt hợp đồng ngay lập tức.",
        },
        headers=employee_token_headers,
    )

    # Without this the test would pass even if the monkeypatch never took effect,
    # because the unflagged path reaches the same LEGAL branch anyway.
    assert calls == ["execute"]
    assert response.status_code == 200
    assert response.json()["agent_role"] == "LEGAL"


def test_enabling_langgraph_logs_how_legal_reviews_contracts(monkeypatch):
    # A local handler rather than the `caplog` fixture, so the test does not depend
    # on pytest's logging plugin being enabled.
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    monkeypatch.setattr(settings, "LANGGRAPH_ENABLED", True)
    logger = logging.getLogger("ai_workforce")
    handler = Collector(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        with TestClient(app):
            pass
    finally:
        logger.removeHandler(handler)

    messages = [record.getMessage() for record in records]
    assert any(
        "LEGAL" in message and "audit_contract_risk" in message for message in messages
    )
