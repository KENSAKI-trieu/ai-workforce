"""IT, Finance, Sales and CEO stay listed but closed until their logic is real.

Each ran on placeholder data: a Jira key no Jira issued, one fixed PO for every invoice,
one camera quoted for every request, and a CEO plan reported as done when nothing had
been done. Neither chat nor the direct APIs may reach that logic any more.
"""

from __future__ import annotations

import json

import pytest

from app.core.agent_status import UNDER_DEVELOPMENT_REPLY, UNDER_DEVELOPMENT_ROLES
from app.core.config import settings
from app.models.models import AgentWorkflow

CARD_FIELDS = ("jira_card", "invoice_card", "quote_card", "dag_plan_card", "approval_card")


@pytest.mark.parametrize("role", sorted(UNDER_DEVELOPMENT_ROLES))
def test_chat_answers_that_the_agent_is_under_development(
    role, client, ceo_token_headers, transactional_db_session
):
    workflows_before = transactional_db_session.query(AgentWorkflow).count()
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": role, "message": "Onboard nhân viên mới, tạo ticket VPN khẩn"},
        headers=ceo_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["agent_role"] == role
    assert data["reply"] == UNDER_DEVELOPMENT_REPLY
    assert data["tools_executed"] == []
    assert all(data[field] is None for field in CARD_FIELDS)
    # The IT placeholder used to record a workflow for every "ticket" it invented.
    assert transactional_db_session.query(AgentWorkflow).count() == workflows_before


@pytest.mark.parametrize("role", sorted(UNDER_DEVELOPMENT_ROLES))
def test_streamed_chat_is_not_sent_to_langgraph(role, client, ceo_token_headers, monkeypatch):
    monkeypatch.setattr(settings, "LANGGRAPH_ENABLED", True)

    def fail(*args, **kwargs):
        raise AssertionError("an under-development agent must not reach LangGraph")

    monkeypatch.setattr(
        "app.services.agents.langgraph_engine.LangGraphEngine.execute_stream", fail
    )
    monkeypatch.setattr("app.services.agents.langgraph_engine.LangGraphEngine.execute", fail)
    response = client.post(
        "/api/v1/agent/chat/stream",
        json={"agent_role": role, "message": "Xin chào"},
        headers=ceo_token_headers,
    )
    assert response.status_code == 200
    payloads = [
        json.loads(line[len("data:"):])
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    replies = [item["reply"] for item in payloads if isinstance(item, dict) and "reply" in item]
    assert replies == [UNDER_DEVELOPMENT_REPLY]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/it/tickets", {"summary": "VPN lỗi"}),
        ("post", "/api/v1/finance/audit-invoice", {"invoice_text": "PO-1 15.000.000 VNĐ"}),
        ("post", "/api/v1/sales/quotation", {"item_query": "20 camera"}),
        ("get", "/api/v1/sales/download-quote/abc123", None),
    ],
)
def test_direct_apis_refuse_while_under_development(method, path, body, client, ceo_token_headers):
    kwargs = {"headers": ceo_token_headers}
    if body is not None:
        kwargs["json"] = body
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 503
    assert "under development" in response.json()["detail"]


def test_agent_list_flags_the_agents_under_development(client, ceo_token_headers):
    response = client.get("/api/v1/agents/", headers=ceo_token_headers)
    assert response.status_code == 200
    flags = {agent["role_code"]: agent["under_development"] for agent in response.json()}
    for role in UNDER_DEVELOPMENT_ROLES:
        assert flags[role] is True
    for role in ("HR", "LEGAL", "KNOWLEDGE"):
        assert flags[role] is False
