"""Legal chat must ask which party the user acts for before reviewing.

Chat used to audit with a hardcoded NEUTRAL perspective while the upload page made
the user choose, so the same contract produced different risk scores depending on
which door it came through, with nothing on screen explaining the gap.
"""

CONTRACT = """
HỢP ĐỒNG DỊCH VỤ PHẦN MỀM
Bên A: Công ty Cung Cấp Gamma
Bên B: Công ty Khách Hàng Delta

Điều 1. Phạm vi dịch vụ
Bên A xây dựng một hệ thống.

Điều 2. Phạt vi phạm
Mức phạt vi phạm là 30% giá trị hợp đồng.

Điều 3. Trách nhiệm
Bên A chịu trách nhiệm không giới hạn với mọi thiệt hại phát sinh.

Điều 4. Chấm dứt
Bên B có quyền đơn phương chấm dứt hợp đồng bất kỳ lúc nào không cần bồi thường.
"""


def _chat(client, headers, message, conversation_id=None):
    payload = {"agent_role": "LEGAL", "message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/v1/agent/chat", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _tool_names(result):
    return [item["tool_name"] for item in result["tools_executed"]]


def test_pasting_a_contract_asks_for_the_perspective_first(client, employee_token_headers):
    result = _chat(client, employee_token_headers, CONTRACT)

    card = result["legal_risk_card"]
    assert card["type"] == "CONTRACT_REVIEW_DRAFT"
    assert card["status"] == "COLLECTING"
    assert "Bên A" in result["reply"] and "Bên B" in result["reply"]
    # Nothing was analysed yet, and the transcript must not claim otherwise.
    assert "audit_contract_risk" not in _tool_names(result)


def test_answering_the_perspective_runs_the_review(client, employee_token_headers):
    first = _chat(client, employee_token_headers, CONTRACT)
    second = _chat(
        client, employee_token_headers, "Tôi là bên B", first["conversation_id"]
    )

    card = second["legal_risk_card"]
    assert _tool_names(second)[0] == "audit_contract_risk"
    assert card["represented_party"] == "PARTY_B"
    assert card["total_risks_found"] > 0
    assert card["review_id"] and card["redline_url"]
    assert "docx_download_url" not in card


def test_perspective_changes_the_assessed_severity(client, employee_token_headers):
    """The direct regression: the answer must reach the analyzer, not be discarded.

    Asserted on the termination finding rather than the aggregate score, because
    this contract is severe enough to hit the 100 cap from either side -- the score
    would hide the very difference being tested.
    """
    first = _chat(client, employee_token_headers, CONTRACT)
    as_party_a = _chat(
        client, employee_token_headers, "Chúng tôi là bên A", first["conversation_id"]
    )
    second = _chat(client, employee_token_headers, CONTRACT)
    as_party_b = _chat(
        client, employee_token_headers, "Mình là khách hàng", second["conversation_id"]
    )

    def termination(result):
        return next(
            item for item in result["legal_risk_card"]["findings"]
            if item["category"] == "TERMINATION" and item["finding_type"] == "COMMERCIAL_RISK"
        )

    assert as_party_a["legal_risk_card"]["represented_party"] == "PARTY_A"
    assert as_party_b["legal_risk_card"]["represented_party"] == "PARTY_B"
    # Bên B holds the unilateral termination right: adverse for B's counterparty.
    assert termination(as_party_a)["severity"] == "HIGH"
    assert termination(as_party_b)["severity"] == "LOW"
    assert termination(as_party_a)["impact"] != termination(as_party_b)["impact"]


def test_seller_phrasing_is_understood_as_party_a(client, employee_token_headers):
    first = _chat(client, employee_token_headers, CONTRACT)
    answered = _chat(
        client, employee_token_headers, "mình là bên bán", first["conversation_id"]
    )
    assert answered["legal_risk_card"]["represented_party"] == "PARTY_A"


def test_neutral_answer_is_understood(client, employee_token_headers):
    first = _chat(client, employee_token_headers, CONTRACT)
    answered = _chat(
        client, employee_token_headers, "trung lập thôi", first["conversation_id"]
    )
    assert answered["legal_risk_card"]["represented_party"] == "NEUTRAL"


def test_unparsable_answer_asks_again_without_reviewing(client, employee_token_headers):
    first = _chat(client, employee_token_headers, CONTRACT)
    retry = _chat(
        client, employee_token_headers, "chưa biết nữa", first["conversation_id"]
    )

    assert retry["legal_risk_card"]["status"] == "COLLECTING"
    assert "audit_contract_risk" not in _tool_names(retry)


def test_cancelling_stops_the_review(client, employee_token_headers):
    first = _chat(client, employee_token_headers, CONTRACT)
    cancelled = _chat(client, employee_token_headers, "hủy", first["conversation_id"])

    assert cancelled["legal_risk_card"]["status"] == "CANCELLED"
    assert "audit_contract_risk" not in _tool_names(cancelled)


def test_an_interruption_never_causes_the_wrong_text_to_be_reviewed(
    client, employee_token_headers
):
    """The fingerprint guard: the reviewed text is the one the question was about.

    The contract is recovered by content hash, so an unrelated message arriving
    between the question and the answer can never be analysed in its place.
    """
    first = _chat(client, employee_token_headers, CONTRACT)
    interruption = "Khoan đã, cho tôi hỏi quy trình nghỉ phép thế nào?"
    _chat(client, employee_token_headers, interruption, first["conversation_id"])
    later = _chat(client, employee_token_headers, "Bên A", first["conversation_id"])

    card = later["legal_risk_card"]
    if "audit_contract_risk" in _tool_names(later):
        # It resumed the original contract, not the interruption.
        assert card["metadata"]["clause_count"] > 1
        # The stored message is stripped, so compare against the stripped original.
        assert later["tools_executed"][0]["input"]["text_length"] == len(CONTRACT.strip())
    else:
        assert card is None or card.get("status") in {"COLLECTING", "AWAITING_INTENT"}


def test_perspective_is_not_asked_when_the_tool_is_disabled(
    client, employee_token_headers, transactional_db_session
):
    from app.models.models import AIAgent, User

    employee = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).one()
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id,
        AIAgent.role_code == "LEGAL",
    ).one()
    original = list(agent.tools_access or [])
    try:
        agent.tools_access = []
        transactional_db_session.commit()

        result = _chat(client, employee_token_headers, CONTRACT)
        assert result["legal_risk_card"] is None
        assert "audit_contract_risk" not in _tool_names(result)
    finally:
        agent.tools_access = original
        transactional_db_session.commit()


def test_high_risk_chat_review_raises_an_approval(client, employee_token_headers):
    """Chat and upload must escalate alike; chat used to escalate nothing."""
    first = _chat(client, employee_token_headers, CONTRACT)
    reviewed = _chat(
        client, employee_token_headers, "Bên A nhé", first["conversation_id"]
    )

    card = reviewed["legal_risk_card"]
    assert card["requires_legal_approval"] is True
    assert card.get("approval_created") is True
    assert card.get("workflow_id")
