"""The routers are wired into the chat turn, and a model cannot overreach through them.

test_legal_llm_flow and test_hr_llm_flow pin the routers in isolation. These go through
`/api/v1/agent/chat` with a fake provider installed, which is the only place that shows
the label actually reaching the branch -- and that a hostile or broken label still cannot
review, cancel or file anything the deterministic rules would not have allowed.
"""

from __future__ import annotations

import json

import pytest

from app.agents.hr.leave import _is_leave_cancel_message, _is_leave_draft_continuation
from app.agents.legal.intent import _classify_legal_contract_intent, _parse_represented_party
from app.agents.text import _normalize_intent_text
from app.agents.hr import llm_flow as hr_llm_flow
from app.agents.legal import llm_flow as legal_llm_flow
from tests.test_legal_chat_perspective import CONTRACT, _chat, _tool_names


class ScriptedAIClient:
    """Answers each router call from a label queue, keyed by the prompt's own shape."""

    enabled = True

    def __init__(self, **labels: object):
        self.labels = labels
        self.calls: list[dict] = []

    def generate_text(self, messages, **_kwargs):
        system = messages[0]["content"]
        try:
            payload = json.loads(messages[-1]["content"])
        except (json.JSONDecodeError, TypeError):
            payload = {"message": messages[-1]["content"]}
        self.calls.append(payload)
        if "which side of a contract" in system:
            answer = self.labels.get("perspective")
        elif "legal assistant" in system:
            answer = self.labels.get("legal_intent")
        elif "leave request" in system:
            answer = self.labels.get("leave_turn")
        elif "intent router for an enterprise HR" in system:
            answer = self.labels.get("hr_intent")
        else:
            # The answer-synthesis slot: leave the governed reply untouched.
            return {"provider": "local", "content": ""}
        if answer is None:
            return {"provider": "local", "content": ""}
        return {"provider": "gemini", "content": json.dumps(answer, ensure_ascii=False)}


@pytest.fixture
def scripted_llm(monkeypatch):
    """Install a fake provider for every router the chat turn may consult."""

    def install(**labels):
        client = ScriptedAIClient(**labels)
        monkeypatch.setattr(legal_llm_flow, "get_ai_service_client", lambda: client)
        monkeypatch.setattr(hr_llm_flow, "get_ai_service_client", lambda: client)
        return client

    return install


QUESTION_WITH_CONTRACT_WORDS = (
    "Rà soát hợp đồng thì công ty mình thường mất bao lâu để Legal phản hồi?"
)


def test_the_router_can_start_a_review_the_scorer_would_have_missed(
    client, employee_token_headers, scripted_llm
):
    """A pasted clause with no numbering: score 1, but it is a contract to review."""
    prose_clause = (
        "Bên A chịu trách nhiệm không giới hạn với mọi thiệt hại phát sinh, và Bên B "
        "có quyền đơn phương chấm dứt bất kỳ lúc nào mà không phải bồi thường."
    )
    assert _classify_legal_contract_intent(prose_clause)[0] == "QUESTION"
    scripted_llm(legal_intent={"intent": "REVIEW"})

    result = _chat(client, employee_token_headers, prose_clause)

    card = result["legal_risk_card"]
    assert card["type"] == "CONTRACT_REVIEW_DRAFT"
    assert card["status"] == "COLLECTING"


def test_the_router_keeps_a_question_out_of_the_review_flow(
    client, employee_token_headers, scripted_llm
):
    scripted_llm(legal_intent={"intent": "QUESTION"})

    result = _chat(client, employee_token_headers, QUESTION_WITH_CONTRACT_WORDS)

    assert result["legal_risk_card"] is None
    assert "audit_contract_risk" not in _tool_names(result)


def test_the_router_reads_a_perspective_the_keyword_rules_cannot(
    client, employee_token_headers, scripted_llm
):
    """"Chúng tôi đi thuê dịch vụ" names no party word, but it says which side they are."""
    scripted_llm(
        legal_intent={"intent": "REVIEW"},
        perspective={"represented_party": "PARTY_B", "decision": "ANSWER"},
    )
    opened = _chat(client, employee_token_headers, CONTRACT)
    reply = "Bọn mình là bên đi thuê dịch vụ và trả tiền"
    assert _parse_represented_party(reply) is None

    answered = _chat(client, employee_token_headers, reply, opened["conversation_id"])

    assert answered["legal_risk_card"]["represented_party"] == "PARTY_B"


def test_a_router_cancellation_stops_the_review(
    client, employee_token_headers, scripted_llm
):
    scripted_llm(
        legal_intent={"intent": "REVIEW"},
        perspective={"represented_party": None, "decision": "CANCEL"},
    )
    opened = _chat(client, employee_token_headers, CONTRACT)

    cancelled = _chat(
        client,
        employee_token_headers,
        "thôi bạn ạ, để tôi hỏi phòng pháp chế đã",
        opened["conversation_id"],
    )

    assert cancelled["legal_risk_card"]["status"] == "CANCELLED"
    assert "audit_contract_risk" not in _tool_names(cancelled)


def test_a_router_answer_cannot_review_without_a_perspective(
    client, employee_token_headers, scripted_llm
):
    """The model claiming an answer with no side named must not audit as NEUTRAL."""
    scripted_llm(
        legal_intent={"intent": "REVIEW"},
        perspective={"represented_party": None, "decision": "ANSWER"},
    )
    opened = _chat(client, employee_token_headers, CONTRACT)

    answered = _chat(
        client, employee_token_headers, "ừ thì sao cũng được", opened["conversation_id"]
    )

    assert "audit_contract_risk" not in _tool_names(answered)
    assert answered["legal_risk_card"]["status"] == "COLLECTING"


def test_the_disabled_tool_gate_runs_before_any_router_call(
    client, employee_token_headers, scripted_llm, transactional_db_session
):
    """A revoked tool must not even be routed for, let alone billed."""
    from app.models.models import AIAgent, User

    employee = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).one()
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id,
        AIAgent.role_code == "LEGAL",
    ).one()
    original = list(agent.tools_access or [])
    fake = scripted_llm(legal_intent={"intent": "REVIEW"})
    try:
        agent.tools_access = []
        transactional_db_session.commit()

        result = _chat(client, employee_token_headers, CONTRACT)

        assert result["legal_risk_card"] is None
        assert fake.calls == []
    finally:
        agent.tools_access = original
        transactional_db_session.commit()


def test_the_router_cancels_a_leave_draft_the_keywords_would_have_kept_open(
    client, employee_token_headers, scripted_llm
):
    scripted_llm(
        hr_intent={"kind": "ACTION", "intent": "ACTION_LEAVE_REQUEST"},
        leave_turn={"turn": "CANCEL"},
    )
    opened = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "HR", "message": "Tôi muốn xin nghỉ phép"},
        headers=employee_token_headers,
    ).json()
    assert opened["hr_card"]["status"] == "COLLECTING"
    wording = "Sếp vừa bảo không cần rồi, bỏ đơn giúp mình nhé"
    # The keyword rules not only miss this cancellation, they read it as the answer to
    # the slot being collected -- so it would have been filed as the reason for leave.
    assert not _is_leave_cancel_message(
        _normalize_intent_text(wording)
    )
    assert _is_leave_draft_continuation(wording, opened["hr_card"])

    cancelled = client.post(
        "/api/v1/agent/chat",
        json={
            "agent_role": "HR",
            "message": wording,
            "conversation_id": opened["conversation_id"],
        },
        headers=employee_token_headers,
    ).json()

    assert cancelled["hr_card"]["status"] == "CANCELLED"


def test_a_policy_question_mid_draft_is_not_filed_as_the_leave_reason(
    client, employee_token_headers, scripted_llm
):
    scripted_llm(
        hr_intent={"kind": "QUESTION", "intent": "POLICY_QUERY"},
        leave_turn={"turn": "UNRELATED"},
    )
    opened = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "HR", "message": "Tôi muốn xin nghỉ phép"},
        headers=employee_token_headers,
    ).json()
    question = "Mà công ty quy định nghỉ phép năm tối đa bao nhiêu ngày?"

    answered = client.post(
        "/api/v1/agent/chat",
        json={
            "agent_role": "HR",
            "message": question,
            "conversation_id": opened["conversation_id"],
        },
        headers=employee_token_headers,
    ).json()

    card = answered.get("hr_card") or {}
    assert card.get("reason") != question
    assert card.get("status") != "SUBMITTED"
