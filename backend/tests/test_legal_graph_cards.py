"""What a Legal turn through LangGraph leaves in the chat: its review card and its sources.

Two gaps between the graph and the deterministic chat. A graph turn that asked for the
user's side left no open question behind, so when the next turn fell back to the
deterministic chat -- which happens whenever the graph's model is down -- "bên B" was read
as a legal question and the contract was never reviewed. And an answer resting on a
search the model ran itself was verified by the graph but reached the user with no sources.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import create_internal_tool_token
from app.models.models import AIAgent, ChatConversation, ChatMessage, User
from app.agents.langgraph.engine import LangGraphEngine
from app.agents.legal.review import _contract_fingerprint, _legal_review_card

CONTRACT = (
    "HỢP ĐỒNG DỊCH VỤ\n"
    "Bên A: Công ty Cung Cấp Gamma\nBên B: Công ty Khách Hàng Delta\n"
    "Điều 1. Phạt vi phạm\nMức phạt 30% giá trị hợp đồng.\n"
    "Điều 2. Trách nhiệm\nBên A chịu trách nhiệm không giới hạn với mọi thiệt hại.\n"
    "Điều 3. Chấm dứt\nBên B đơn phương chấm dứt bất kỳ lúc nào."
)


@pytest.fixture(scope="module")
def employee(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "employee@company.com").one()


@pytest.fixture(scope="module")
def legal_agent(transactional_db_session, employee):
    return transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id, AIAgent.role_code == "LEGAL"
    ).one()


def _conversation(db, user, agent, contract):
    conversation = ChatConversation(
        tenant_id=user.tenant_id, user_id=user.id, ai_agent_id=agent.id, title="graph turn"
    )
    db.add(conversation)
    db.flush()
    conversation.thread_id = str(conversation.id)
    db.add(ChatMessage(
        conversation_id=conversation.id,
        sender="USER",
        content=contract,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    ))
    db.flush()
    return conversation


def _graph_turn_asking_for_the_side(client, db, user, conversation):
    """The tool as the graph ran it, wrapped in the result the AI service returns."""
    token = create_internal_tool_token(user, agent_role="LEGAL")
    response = client.post(
        "/api/v1/internal/tools/audit_contract_risk/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"input": {
            "tenant_id": str(user.tenant_id),
            "audit": {"correlation_id": str(uuid.uuid4()), "conversation_id": str(conversation.id)},
        }},
    )
    assert response.status_code == 200, response.text
    outcome = response.json()["result"]
    assert outcome["status"] == "NEEDS_REPRESENTED_PARTY"
    return {"state": {"tool_calls": [
        {"name": "audit_contract_risk", "status": "SUCCESS", "terminal": True, "result": outcome}
    ]}}


def _persist_assistant(db, conversation, reply, card):
    db.add(ChatMessage(
        conversation_id=conversation.id,
        sender="ASSISTANT",
        content=reply,
        attachments=[{"type": "LEGAL_RISK_CARD", "payload": card}] if card else [],
        created_at=datetime.now(timezone.utc) - timedelta(minutes=9),
    ))
    db.flush()


def test_a_graph_turn_asking_for_the_side_leaves_the_question_open(
    client, transactional_db_session, employee, legal_agent, employee_token_headers
):
    db = transactional_db_session
    conversation = _conversation(db, employee, legal_agent, CONTRACT)
    result = _graph_turn_asking_for_the_side(client, db, employee, conversation)

    card = LangGraphEngine.legal_risk_card(
        db, employee, result, agent=legal_agent, conversation_id=str(conversation.id)
    )

    assert card["type"] == "CONTRACT_REVIEW_DRAFT"
    assert card["status"] == "COLLECTING"
    assert card["contract_char_count"] == len(CONTRACT)
    assert card["document_scope"] == "FULL"

    # The next turn falls back to the deterministic chat, which finds the open question
    # and reviews the contract from the side the user names.
    _persist_assistant(db, conversation, "Bạn đại diện cho bên nào?", card)
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "LEGAL", "message": "bên B", "conversation_id": str(conversation.id)},
        headers=employee_token_headers,
    )
    assert response.status_code == 200, response.text
    reply = response.json()
    assert reply["legal_risk_card"]["represented_party"] == "PARTY_B"
    assert reply["legal_risk_card"]["review_id"]


def test_a_graph_turn_that_moves_on_closes_an_open_question(
    transactional_db_session, employee, legal_agent
):
    db = transactional_db_session
    conversation = _conversation(db, employee, legal_agent, CONTRACT)
    _persist_assistant(
        db,
        conversation,
        "Bạn đại diện cho bên nào?",
        _legal_review_card(
            status="COLLECTING",
            contract_fingerprint=_contract_fingerprint(CONTRACT),
            contract_char_count=len(CONTRACT),
        ),
    )
    answered_something_else = {"state": {"tool_calls": [], "final_answer": "Theo quy chế..."}}

    card = LangGraphEngine.legal_risk_card(
        db, employee, answered_something_else,
        agent=legal_agent, conversation_id=str(conversation.id),
    )

    assert card["type"] == "CONTRACT_REVIEW_DRAFT"
    assert card["status"] == "DISMISSED"


def test_a_turn_with_nothing_open_carries_no_card(transactional_db_session, employee, legal_agent):
    db = transactional_db_session
    conversation = _conversation(db, employee, legal_agent, "Luật áp dụng là gì?")

    assert LangGraphEngine.legal_risk_card(
        db, employee, {"state": {"tool_calls": []}},
        agent=legal_agent, conversation_id=str(conversation.id),
    ) is None


def test_sources_from_a_search_the_model_ran_are_shown():
    state = {
        "retrieved_context": [{"id": "c1", "document_title": "Quy chế A"}],
        "tool_calls": [
            {"name": "rag_search", "status": "SUCCESS",
             "result": [{"id": "c9", "document_title": "Luật B", "citation_tag": "[Luật B]"}]},
            {"name": "rag_search", "status": "FAILED", "result": None},
        ],
        "citations": [{"source": "Luật B", "chunk_id": "c9"}],
    }

    assert LangGraphEngine.public_citations(state) == [
        {"id": "c9", "document_title": "Luật B", "citation_tag": "[Luật B]"}
    ]


def test_an_answer_citing_only_in_its_text_still_shows_the_source():
    """Live 2026-09-29: the model's citation object held the document and section but no
    chunk, the full tag was in the answer, and the verified answer showed no source."""
    chunk = {"id": "7e10d4a1", "document_title": "Quy chế quản lý hợp đồng", "section_title": "Điều 5"}
    other = {"id": "8219c43d", "document_title": "Quy chế quản lý hợp đồng", "section_title": "Điều 8"}
    tag = "[Citation: Quy chế quản lý hợp đồng, v1.0, 3. Hợp đồng từ 2 tỷ đồng trở lên: Tổng Giám đốc ký.; chunk=7e10d4a1]"
    state = {
        "retrieved_context": [other, chunk],
        "citations": [{"source": "Quy chế quản lý hợp đồng, v1.0, 3. Hợp đồng từ 2 tỷ đồng trở lên: Tổng Giám đốc ký."}],
        "final_answer": f"Hợp đồng từ 2 tỷ đồng trở lên do Tổng Giám đốc ký {tag}.",
    }

    assert LangGraphEngine.public_citations(state) == [chunk]


def test_a_citation_naming_only_its_document_shows_one_of_its_chunks():
    chunk = {"id": "c1", "document_title": "Quy chế A"}
    state = {
        "retrieved_context": [chunk, {"id": "c2", "document_title": "Quy chế A"}],
        "citations": [{"source": "Quy chế A, v1.0, Điều 8"}],
        "final_answer": "Tạm ứng tối đa 30%.",
    }

    assert LangGraphEngine.public_citations(state) == [chunk]
