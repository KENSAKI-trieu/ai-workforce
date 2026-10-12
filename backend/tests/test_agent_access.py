"""Who may chat with an agent meant for some positions only.

The CEO assistant opens to positions ticked "Dùng Trợ lý CEO" (executive.briefing.view).
The box was listed as "Sắp có" while nothing read it. Now the chat, its stream and a
regenerated answer all refuse a position without it, and the agent list tells the sidebar
to hide the agent. Every other agent stays open to every member: what a turn may read or
do is decided tool by tool.
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.chat import CEO_UNAVAILABLE_REPLY
from app.core.permissions import permission_catalog
from app.domains.platform.agent_access import (
    AGENT_USE_PERMISSIONS,
    AGENT_USE_REFUSED,
    can_use_agent,
)
from app.models.models import AIAgent, ChatConversation, ChatMessage, User

CEO_BOX = AGENT_USE_PERMISSIONS["CEO"]


@pytest.fixture
def employee(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "employee@company.com").one()


def _conversations(db, user) -> int:
    return db.query(ChatConversation).filter(ChatConversation.user_id == user.id).count()


def test_both_ceo_boxes_are_read_now():
    boxes = {item["code"]: item for item in permission_catalog()}
    assert CEO_BOX == "executive.briefing.view"
    assert boxes[CEO_BOX]["coming_soon"] is False
    # Checked by the alert settings since C4 (api/v1/executive.py).
    assert boxes["executive.alerts.manage"]["coming_soon"] is False


@pytest.mark.parametrize("path", ["/api/v1/agent/chat", "/api/v1/agent/chat/stream"])
def test_a_position_without_the_box_cannot_chat_with_the_ceo_agent(
    path, client, employee_token_headers, transactional_db_session, employee
):
    assert not can_use_agent(transactional_db_session, employee, "CEO")
    before = _conversations(transactional_db_session, employee)

    response = client.post(
        path,
        json={"agent_role": "CEO", "message": "Tóm tắt tình hình công ty tuần này"},
        headers=employee_token_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == AGENT_USE_REFUSED
    # Refused before the conversation is opened.
    assert _conversations(transactional_db_session, employee) == before


def test_regenerating_a_ceo_answer_needs_the_box_too(
    client, employee_token_headers, transactional_db_session, employee
):
    # A conversation left from before the box was read.
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id, AIAgent.role_code == "CEO"
    ).one()
    conversation_id = uuid.uuid4()
    transactional_db_session.add(ChatConversation(
        id=conversation_id,
        tenant_id=employee.tenant_id,
        user_id=employee.id,
        ai_agent_id=agent.id,
        title="Bản tin tuần",
        thread_id=str(conversation_id),
    ))
    transactional_db_session.add(ChatMessage(
        conversation_id=conversation_id, sender="USER", content="Tóm tắt tuần này"
    ))
    transactional_db_session.commit()

    response = client.post(
        f"/api/v1/agent/conversations/{conversation_id}/regenerate",
        headers=employee_token_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == AGENT_USE_REFUSED


def test_the_agent_list_tells_who_may_use_the_ceo_agent(
    client, employee_token_headers, ceo_token_headers
):
    def flags(headers) -> dict[str, bool]:
        response = client.get("/api/v1/agents/", headers=headers)
        assert response.status_code == 200
        return {agent["role_code"]: agent["can_use"] for agent in response.json()}

    employee_flags = flags(employee_token_headers)
    assert employee_flags["CEO"] is False
    assert all(allowed for role, allowed in employee_flags.items() if role != "CEO")
    assert flags(ceo_token_headers)["CEO"] is True

    one = client.get("/api/v1/agents/CEO", headers=employee_token_headers)
    assert one.status_code == 200
    assert one.json()["can_use"] is False


def test_ticking_the_box_on_a_position_opens_the_ceo_agent(
    client, employee_token_headers, transactional_db_session, employee
):
    position = employee.position
    assert position is not None
    original = list(position.permissions or [])
    try:
        position.permissions = sorted({*original, CEO_BOX})
        transactional_db_session.flush()
        assert can_use_agent(transactional_db_session, employee, "CEO")

        response = client.post(
            "/api/v1/agent/chat",
            json={"agent_role": "CEO", "message": "Tóm tắt tuần này"},
            headers=employee_token_headers,
        )
        # Through the gate. No AI service runs under the tests, so the assistant says so.
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == CEO_UNAVAILABLE_REPLY
    finally:
        position.permissions = original
        transactional_db_session.flush()
