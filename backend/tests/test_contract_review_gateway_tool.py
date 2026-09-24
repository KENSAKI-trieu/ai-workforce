"""`audit_contract_risk` through the tool gateway: the LangGraph path to contract review.

With LANGGRAPH_ENABLED the backend's Legal chat branch is skipped, and this tool is the
only way the graph can review a contract. It reads the text from the user's own message
rather than from the model, reads the user's side from their own words -- never from the
model, which filled in NEUTRAL for a user who had not answered -- and stores and
escalates the review the way the deterministic chat does.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import create_internal_tool_token
from app.models.models import AIAgent, ChatConversation, ChatMessage, ContractReview, User
from app.services.agents import legal_llm_flow
from app.services.agents.langgraph_engine import LangGraphEngine

CONTRACT = (
    "HỢP ĐỒNG DỊCH VỤ\n"
    "Điều 1. Phạt vi phạm\nMức phạt 30% giá trị hợp đồng.\n"
    "Điều 2. Trách nhiệm\nBên A chịu trách nhiệm không giới hạn với mọi thiệt hại.\n"
    "Điều 3. Chấm dứt\nBên B đơn phương chấm dứt bất kỳ lúc nào."
)


@pytest.fixture(scope="module")
def employee(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "employee@company.com").one()


def _conversation(db, user, *user_messages):
    """A conversation holding these user messages, oldest first.

    Timestamps are explicit: rows written in one test transaction share now(), and the
    tool counts messages back from the newest.
    """
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id, AIAgent.role_code == "LEGAL"
    ).one()
    conversation = ChatConversation(
        tenant_id=user.tenant_id, user_id=user.id, ai_agent_id=agent.id, title="review"
    )
    db.add(conversation)
    db.flush()
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    for index, content in enumerate(user_messages):
        db.add(ChatMessage(
            conversation_id=conversation.id,
            sender="USER",
            content=content,
            created_at=start + timedelta(minutes=index),
        ))
    db.flush()
    return conversation


def _invoke(client, user, conversation_id, **arguments):
    token = create_internal_tool_token(user, agent_role="LEGAL")
    return client.post(
        "/api/v1/internal/tools/audit_contract_risk/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"input": {
            "tenant_id": str(user.tenant_id),
            "audit": {
                "correlation_id": str(uuid.uuid4()),
                "conversation_id": str(conversation_id),
            },
            **arguments,
        }},
    )


def test_the_tool_reviews_exactly_what_the_user_sent(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, CONTRACT, "bên A")

    response = _invoke(client, employee, conversation.id, from_user_message=1)

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["status"] == "REVIEWED"
    assert result["reviewed_characters"] == len(CONTRACT)
    assert result["document_scope"] == "FULL"
    assert result["represented_party"].startswith("Bên A")
    review = transactional_db_session.get(ContractReview, uuid.UUID(result["review_id"]))
    assert review.contract_text == CONTRACT
    assert review.created_by_id == employee.id
    # CRITICAL findings escalate as they do in the deterministic chat.
    assert result["approval_created"] is True
    assert review.workflow_id is not None
    # The graph ends the turn on this, in the deterministic chat's own words.
    assert result["reply"].startswith("Tôi đã rà soát nội dung hợp đồng theo góc nhìn")


def test_the_users_side_is_asked_for_never_guessed(client, transactional_db_session, employee):
    """The contract names Bên A and Bên B; neither is the user saying who they are."""
    conversation = _conversation(transactional_db_session, employee, CONTRACT + "\nĐiều 4. X\nY.")
    before = transactional_db_session.query(ContractReview).count()

    response = _invoke(client, employee, conversation.id)

    result = response.json()["result"]
    assert result["status"] == "NEEDS_REPRESENTED_PARTY"
    assert "bạn đại diện cho bên nào" in result["reply"]
    assert transactional_db_session.query(ContractReview).count() == before


def test_a_side_stated_with_the_contract_is_read_by_the_model(
    client, transactional_db_session, employee, monkeypatch
):
    class Reader:
        enabled = True

        def generate_text(self, messages, **_kwargs):
            assert "Tôi là bên B" in json.loads(messages[-1]["content"])["message"]
            reply = {"represented_party": "PARTY_B", "decision": "ANSWER"}
            return {"provider": "gemini", "content": json.dumps(reply)}

    monkeypatch.setattr(legal_llm_flow, "get_ai_service_client", lambda: Reader())
    conversation = _conversation(
        transactional_db_session, employee, "Tôi là bên B. Rà soát giúp:\n" + CONTRACT
    )

    result = _invoke(client, employee, conversation.id).json()["result"]

    assert result["status"] == "REVIEWED"
    assert result["represented_party"].startswith("Bên B")


def test_an_answer_naming_a_side_reviews_the_message_before_it(
    client, transactional_db_session, employee
):
    conversation = _conversation(transactional_db_session, employee, CONTRACT, "tôi là bên B")

    result = _invoke(client, employee, conversation.id, from_user_message=1).json()["result"]

    assert result["reviewed_characters"] == len(CONTRACT)
    assert result["represented_party"].startswith("Bên B")


def test_another_users_conversation_cannot_be_reviewed(client, transactional_db_session, employee):
    other = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    conversation = _conversation(transactional_db_session, other, CONTRACT, "bên A")

    response = _invoke(client, employee, conversation.id, from_user_message=1)

    assert response.status_code == 404


def test_the_tool_obeys_the_legal_agents_grant(client, transactional_db_session, employee):
    legal = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id, AIAgent.role_code == "LEGAL"
    ).one()
    saved = (legal.tools_access, legal.allowed_actions)
    legal.tools_access = legal.allowed_actions = ["hybrid_rag_search", "rag_search"]
    transactional_db_session.flush()
    try:
        conversation = _conversation(transactional_db_session, employee, CONTRACT, "bên A")
        response = _invoke(client, employee, conversation.id, from_user_message=1)
    finally:
        legal.tools_access, legal.allowed_actions = saved
        transactional_db_session.flush()

    assert response.status_code == 403


def test_the_chat_card_is_rebuilt_from_the_saved_review(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, CONTRACT + "\n", "bên A")
    review_id = _invoke(
        client, employee, conversation.id, from_user_message=1
    ).json()["result"]["review_id"]

    def run(result, user):
        return LangGraphEngine.legal_risk_card(transactional_db_session, user, {
            "state": {"tool_calls": [
                {"name": "rag_search", "status": "SUCCESS", "result": []},
                {"name": "audit_contract_risk", "status": "SUCCESS", "result": result},
            ]},
        })

    card = run({"review_id": review_id}, employee)
    assert card["review_id"] == review_id
    assert card["findings"] and card["redline_url"].endswith(f"{review_id}/redline")
    # No card for a call that reviewed nothing, or for someone else's review.
    assert run({"status": "NEEDS_REPRESENTED_PARTY"}, employee) is None
    admin = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    assert run({"review_id": review_id}, admin) is None
