"""Legal chat must not confuse a question with a contract, in either direction.

The rule this replaces was `len(message) >= 180`: a long question was sent to the
analyzer, and a short pasted clause fell through to a four-term glossary that
answered "chưa tìm thấy văn bản phù hợp" -- leaving the user believing their
contract had been reviewed when nothing had been.
"""

from app.services.agents.agent_executor import _classify_legal_contract_intent


LONG_QUESTION = (
    "Cho tôi hỏi về quy trình nội bộ: khi phòng ban muốn ký kết với một nhà cung cấp "
    "mới thì cần chuẩn bị những giấy tờ gì, ai là người có thẩm quyền phê duyệt, và "
    "thời gian xử lý trung bình là bao lâu? Tôi muốn nắm rõ để chuẩn bị cho quý sau."
)

SHORT_CLAUSE = (
    "HỢP ĐỒNG\n"
    "Điều 1. Phạt\nPhạt 30% giá trị.\n"
    "Điều 2. Chấm dứt\nBên A chấm dứt bất kỳ lúc nào.\n"
    "Điều 3. Bảo mật\nHai bên giữ bí mật."
)

# Reads like contract prose but has no clause structure and asks nothing: the case
# where guessing either way would be wrong, so the agent must ask.
AMBIGUOUS = (
    "Căn cứ Bộ luật Dân sự, các bên thỏa thuận mức phạt vi phạm "
    "là 30% giá trị hợp đồng đã ký."
)


def _chat(client, headers, message, conversation_id=None):
    payload = {"agent_role": "LEGAL", "message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/v1/agent/chat", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _tool_names(result):
    return [item["tool_name"] for item in result["tools_executed"]]


def test_long_question_is_not_treated_as_a_contract():
    assert len(LONG_QUESTION) > 180  # the old rule would have audited this
    intent, signals = _classify_legal_contract_intent(LONG_QUESTION)
    assert intent == "QUESTION"
    assert signals["interrogative"] is True


def test_short_pasted_clause_is_recognised_as_a_contract():
    assert len(SHORT_CLAUSE) < 180  # the old rule would have missed this
    intent, signals = _classify_legal_contract_intent(SHORT_CLAUSE)
    assert intent == "REVIEW"
    assert signals["clause_structure"] is True


def test_ambiguous_text_is_neither_reviewed_nor_silently_dropped():
    intent, _ = _classify_legal_contract_intent(AMBIGUOUS)
    assert intent == "UNSURE"


def test_long_question_gets_a_notice_that_nothing_was_reviewed(
    client, employee_token_headers
):
    result = _chat(client, employee_token_headers, LONG_QUESTION)

    assert "audit_contract_risk" not in _tool_names(result)
    assert result["legal_risk_card"] is None
    # The user must never be left thinking a review happened.
    assert "chưa rà soát rủi ro nội dung này" in result["reply"]


def test_short_clause_reaches_the_perspective_question(client, employee_token_headers):
    result = _chat(client, employee_token_headers, SHORT_CLAUSE)

    assert result["legal_risk_card"]["status"] == "COLLECTING"
    assert "audit_contract_risk" not in _tool_names(result)


def test_unsure_turn_asks_and_can_be_confirmed(client, employee_token_headers):
    first = _chat(client, employee_token_headers, AMBIGUOUS)
    assert first["legal_risk_card"]["status"] == "AWAITING_INTENT"
    assert "audit_contract_risk" not in _tool_names(first)

    confirmed = _chat(
        client, employee_token_headers, "rà soát", first["conversation_id"]
    )
    assert confirmed["legal_risk_card"]["status"] == "COLLECTING"


def test_a_pasted_clause_is_never_answered_from_the_glossary(
    client, employee_token_headers
):
    """A glossary definition in place of a review is the failure being prevented."""
    clause_about_indemnification = (
        "HỢP ĐỒNG NGUYÊN TẮC\n"
        "Điều 1. Indemnification\nBên A bồi thường mọi khiếu nại.\n"
        "Điều 2. Phạt vi phạm\nMức phạt 25% giá trị hợp đồng.\n"
        "Điều 3. Chấm dứt\nBên B đơn phương chấm dứt bất kỳ lúc nào."
    )
    result = _chat(client, employee_token_headers, clause_about_indemnification)

    assert "legal_glossary_lookup" not in _tool_names(result)
    assert result["legal_risk_card"]["status"] == "COLLECTING"
