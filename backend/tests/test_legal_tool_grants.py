"""The Legal agent does what its tool grants say, and the configuration page offers only
toggles that change something.

Before: switching `hybrid_rag_search` off left the Legal chat searching; switching
`audit_contract_risk` off silenced questions as well as reviews; the /legal/* endpoints
ignored the grants entirely; and the page offered HR tools on the Legal agent that no
Legal branch ever checked.
"""

from __future__ import annotations

import contextlib

import pytest

from app.models.models import AIAgent, User

SHORT_CONTRACT = (
    "HỢP ĐỒNG\n"
    "Điều 1. Phạt\nPhạt 30% giá trị.\n"
    "Điều 2. Chấm dứt\nBên A chấm dứt bất kỳ lúc nào.\n"
    "Điều 3. Bảo mật\nHai bên giữ bí mật."
)
QUESTION = "Nhân viên được nghỉ phép bao nhiêu ngày một năm?"


@pytest.fixture(scope="module")
def legal(transactional_db_session):
    admin = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    return transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == admin.tenant_id, AIAgent.role_code == "LEGAL"
    ).one()


@contextlib.contextmanager
def granted(db, agent, tools):
    """Grant exactly these tools for the block, then put the shared row back."""
    saved = (agent.tools_access, agent.allowed_actions, agent.disallowed_actions)
    agent.tools_access, agent.allowed_actions, agent.disallowed_actions = tools, tools, []
    db.flush()
    try:
        yield
    finally:
        agent.tools_access, agent.allowed_actions, agent.disallowed_actions = saved
        db.flush()


def _chat(client, headers, message, conversation_id=None):
    payload = {"agent_role": "LEGAL", "message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/v1/agent/chat", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _tools(result):
    return [item["tool_name"] for item in result["tools_executed"]]


# --- The chat ------------------------------------------------------------------


def test_questions_still_work_with_reviews_switched_off(
    client, employee_token_headers, transactional_db_session, legal
):
    with granted(transactional_db_session, legal, ["hybrid_rag_search"]):
        answered = _chat(client, employee_token_headers, QUESTION)
        pasted = _chat(client, employee_token_headers, SHORT_CONTRACT)

    assert "hybrid_rag_search" in _tools(answered)
    assert "audit_contract_risk" not in _tools(pasted)
    assert "`audit_contract_risk` hiện chưa được bật" in pasted["reply"]
    assert pasted["legal_risk_card"] is None


def test_switching_search_off_stops_the_search(
    client, employee_token_headers, transactional_db_session, legal
):
    with granted(transactional_db_session, legal, ["audit_contract_risk"]):
        result = _chat(client, employee_token_headers, QUESTION)

    assert "hybrid_rag_search" not in _tools(result)
    assert "`hybrid_rag_search` hiện chưa được bật" in result["reply"]
    assert result["citations"] == []


def test_with_no_tools_the_agent_says_so_and_does_nothing(
    client, employee_token_headers, transactional_db_session, legal
):
    with granted(transactional_db_session, legal, []):
        result = _chat(client, employee_token_headers, QUESTION)

    assert _tools(result) == []
    assert "chưa được bật công cụ nào" in result["reply"]


def test_revoking_reviews_mid_conversation_closes_the_open_review(
    client, employee_token_headers, transactional_db_session, legal
):
    opened = _chat(client, employee_token_headers, SHORT_CONTRACT)
    assert opened["legal_risk_card"]["status"] == "COLLECTING"

    with granted(transactional_db_session, legal, ["hybrid_rag_search"]):
        # A side named after the revocation must not run the review.
        result = _chat(client, employee_token_headers, "Bên A", opened["conversation_id"])

    assert "audit_contract_risk" not in _tools(result)
    assert result["legal_risk_card"]["status"] == "DISMISSED"


# --- The /legal/* endpoints ------------------------------------------------------


def test_legal_endpoints_refuse_a_tool_that_is_switched_off(
    client, ceo_token_headers, transactional_db_session, legal
):
    body = {"contract_text": SHORT_CONTRACT, "document_name": "x", "represented_party": "PARTY_A"}
    with granted(transactional_db_session, legal, ["hybrid_rag_search"]):
        refused = client.post("/api/v1/legal/audit-contract", json=body, headers=ceo_token_headers)
    allowed = client.post("/api/v1/legal/audit-contract", json=body, headers=ceo_token_headers)

    assert refused.status_code == 403
    assert "audit_contract_risk" in refused.json()["detail"]
    assert allowed.status_code == 200, allowed.text


@pytest.mark.parametrize(
    "path,tool",
    [
        ("/api/v1/legal/compare-documents", "compare_contract_versions"),
        ("/api/v1/legal/privacy-check", "check_sensitive_data"),
        ("/api/v1/legal/license-check", "check_software_licenses"),
    ],
)
def test_each_legal_file_endpoint_is_gated_by_its_own_tool(
    client, ceo_token_headers, transactional_db_session, legal, path, tool
):
    with granted(transactional_db_session, legal, ["audit_contract_risk"]):
        # Refused before the upload is even read, so no file is needed.
        response = client.post(path, headers=ceo_token_headers)

    assert response.status_code == 403
    assert tool in response.json()["detail"]


# --- The configuration page --------------------------------------------------------


def test_the_page_offers_only_tools_the_legal_agent_uses(
    client, ceo_token_headers, transactional_db_session, legal
):
    with granted(transactional_db_session, legal, ["audit_contract_risk", "request_leave"]):
        options = client.get(
            "/api/v1/agents/LEGAL/configuration-options", headers=ceo_token_headers
        ).json()
        kept = client.patch(
            "/api/v1/agents/LEGAL",
            json={"tools_access": ["audit_contract_risk", "request_leave"],
                  "allowed_actions": ["audit_contract_risk", "request_leave"]},
            headers=ceo_token_headers,
        )
        added = client.patch(
            "/api/v1/agents/LEGAL",
            json={"tools_access": ["audit_contract_risk", "query_leave_balance"],
                  "allowed_actions": ["audit_contract_risk", "query_leave_balance"]},
            headers=ceo_token_headers,
        )

    offered = {tool["name"] for tool in options["tools"]}
    assert "request_leave" not in offered and "query_leave_balance" not in offered
    assert {"audit_contract_risk", "hybrid_rag_search", "rag_search"} <= offered
    assert options["unsupported_grants"] == ["request_leave"]
    # An old grant does not block saving; a new one the role cannot use is refused.
    assert kept.status_code == 200, kept.text
    assert added.status_code == 422
    assert "query_leave_balance" in added.json()["detail"]


def test_hr_is_not_offered_gateway_tools_it_never_runs(client, ceo_token_headers):
    options = client.get("/api/v1/agents/HR/configuration-options", headers=ceo_token_headers).json()

    assert "rag_search" not in {tool["name"] for tool in options["tools"]}
    assert "request_leave" in {tool["name"] for tool in options["tools"]}
