"""Legal chat must not confuse a question with a contract, in either direction.

The rule this replaces was `len(message) >= 180`: a long question was sent to the
analyzer, and a short pasted clause fell through to a four-term glossary that
answered "chưa tìm thấy văn bản phù hợp" -- leaving the user believing their
contract had been reviewed when nothing had been.
"""

import logging

import pytest

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


def _routing_log(caplog):
    return [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Legal ")
    ]


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


# --- Replies to "review this, or were you asking?" -----------------------------

FULL_VERSION_REPLY = (
    "Đây là bản đầy đủ, rà soát giúp tôi:\n"
    "HỢP ĐỒNG DỊCH VỤ\n"
    "Điều 1. Phạt vi phạm\nMức phạt 30% giá trị hợp đồng.\n"
    "Điều 2. Chấm dứt\nBên A đơn phương chấm dứt bất kỳ lúc nào.\n"
    "Điều 3. Bảo mật\nHai bên giữ bí mật trong 5 năm."
)


class ScriptedRouter:
    """Answers the Legal router with fixed labels and counts every billed call.

    A label may be a bare intent or a whole reply object, for replies carrying a scope.
    """

    enabled = True

    def __init__(self, **labels_by_pending_state):
        self.labels = labels_by_pending_state
        self.pending_states: list[str] = []

    def generate_text(self, messages, **_kwargs):
        import json

        pending_state = json.loads(messages[-1]["content"])["pending_state"]
        self.pending_states.append(pending_state)
        label = self.labels[pending_state]
        reply = label if isinstance(label, dict) else {"intent": label}
        return {"provider": "gemini", "content": json.dumps(reply)}


def _route_with(monkeypatch, router):
    from app.services.agents import legal_llm_flow

    monkeypatch.setattr(legal_llm_flow, "get_ai_service_client", lambda: router)


def _open_awaiting_intent(client, headers):
    first = _chat(client, headers, AMBIGUOUS)
    assert first["legal_risk_card"]["status"] == "AWAITING_INTENT"
    return first["conversation_id"]


def _assert_reviews_the_new_text(client, headers, conversation_id, reply):
    from app.services.agents.agent_executor import _contract_fingerprint

    # The card must point at the text just sent, not at the one the question was about.
    assert reply["legal_risk_card"]["status"] == "COLLECTING"
    assert reply["legal_risk_card"]["contract_fingerprint"] == _contract_fingerprint(
        FULL_VERSION_REPLY
    )
    reviewed = _chat(client, headers, "Bên A", conversation_id)
    audit = next(
        item for item in reviewed["tools_executed"]
        if item["tool_name"] == "audit_contract_risk"
    )
    assert audit["input"]["text_length"] == len(FULL_VERSION_REPLY)


def test_a_fuller_version_sent_as_the_answer_is_what_gets_reviewed(
    client, employee_token_headers
):
    """Without a model: the keyword reading must not take "rà soát giúp" as a yes."""
    conversation_id = _open_awaiting_intent(client, employee_token_headers)

    reply = _chat(client, employee_token_headers, FULL_VERSION_REPLY, conversation_id)

    _assert_reviews_the_new_text(client, employee_token_headers, conversation_id, reply)


def test_a_model_yes_to_a_reply_carrying_a_contract_reviews_that_contract(
    client, employee_token_headers, monkeypatch
):
    conversation_id = _open_awaiting_intent(client, employee_token_headers)
    router = ScriptedRouter(AWAITING_INTENT="CONFIRM_REVIEW")
    _route_with(monkeypatch, router)

    reply = _chat(client, employee_token_headers, FULL_VERSION_REPLY, conversation_id)

    assert router.pending_states == ["AWAITING_INTENT"]
    monkeypatch.undo()
    _assert_reviews_the_new_text(client, employee_token_headers, conversation_id, reply)


def test_declining_the_review_is_acknowledged_not_searched(
    client, employee_token_headers, caplog
):
    conversation_id = _open_awaiting_intent(client, employee_token_headers)
    caplog.set_level(logging.INFO, logger="app.services.agents.agent_executor")
    caplog.clear()

    reply = _chat(client, employee_token_headers, "không cần đâu", conversation_id)

    assert reply["legal_risk_card"]["status"] == "DISMISSED"
    assert _routing_log(caplog) == [
        "Legal turn routed: pending_state=AWAITING_INTENT source=fallback "
        "label=DECLINE_REVIEW keyword=QUESTION",
    ]
    assert "hybrid_rag_search" not in _tool_names(reply)
    assert "không rà soát nội dung đó" in reply["reply"]


@pytest.mark.parametrize("label", ["DECLINE_REVIEW", "CANCEL"])
def test_a_model_decline_is_acknowledged_with_a_single_router_call(
    client, employee_token_headers, monkeypatch, label
):
    conversation_id = _open_awaiting_intent(client, employee_token_headers)
    router = ScriptedRouter(AWAITING_INTENT=label, NONE="QUESTION")
    _route_with(monkeypatch, router)

    reply = _chat(client, employee_token_headers, "thôi, không cần", conversation_id)

    # The turn is routed once; it used to be classified again with nothing pending.
    assert router.pending_states == ["AWAITING_INTENT"]
    assert reply["legal_risk_card"]["status"] == "DISMISSED"
    assert "hybrid_rag_search" not in _tool_names(reply)


def test_a_new_question_in_reply_is_answered_with_a_single_router_call(
    client, employee_token_headers, monkeypatch
):
    conversation_id = _open_awaiting_intent(client, employee_token_headers)
    router = ScriptedRouter(AWAITING_INTENT="QUESTION", NONE="REVIEW")
    _route_with(monkeypatch, router)

    reply = _chat(
        client,
        employee_token_headers,
        "Thời hạn bảo hành tối thiểu theo luật là bao lâu?",
        conversation_id,
    )

    assert router.pending_states == ["AWAITING_INTENT"]
    assert "hybrid_rag_search" in _tool_names(reply)
    assert reply["legal_risk_card"]["status"] == "DISMISSED"


def test_a_pasted_contract_with_a_question_is_answered_when_the_model_says_so(
    client, employee_token_headers, monkeypatch, caplog
):
    """"...Điều 3 có hợp lệ không? Tôi không cần rà soát cả hợp đồng." is a question.

    Clause numbering used to overrule the model here and ask again, although the user
    had already said which they wanted.
    """
    caplog.set_level(logging.INFO, logger="app.services.agents.agent_executor")
    caplog.clear()
    _route_with(monkeypatch, ScriptedRouter(NONE="QUESTION"))

    reply = _chat(
        client,
        employee_token_headers,
        SHORT_CLAUSE + "\nĐiều 1 có hợp lệ không? Tôi không cần rà soát cả hợp đồng.",
    )

    assert reply["legal_risk_card"] is None
    assert "hybrid_rag_search" in _tool_names(reply)
    assert _routing_log(caplog) == [
        "Legal turn routed: pending_state=NONE source=llm label=QUESTION keyword=REVIEW",
    ]
    # Routing is not a tool: it must not appear as one, nor flip the stream to TOOL_CALLING.
    assert "legal_intent_router" not in _tool_names(reply)


def test_a_question_about_a_clause_is_still_answered_when_the_model_says_so(
    client, employee_token_headers, monkeypatch
):
    """Keyword hits without document structure do not overrule the model."""
    question = "Kiểm tra giúp tôi điều khoản phạt 30% có đúng luật không?"
    _route_with(monkeypatch, ScriptedRouter(NONE="QUESTION"))

    reply = _chat(client, employee_token_headers, question)

    assert reply["legal_risk_card"] is None
    assert "hybrid_rag_search" in _tool_names(reply)


# --- The keyword floor: whole words, not substrings ----------------------------


def test_contract_prose_granting_a_right_is_not_read_as_a_question():
    """ "Bên B có được quyền ..." is a grant; "phát triển" is not a penalty."""
    prose = (
        "Căn cứ Luật Thương mại, các bên thỏa thuận: Bên B có được quyền phát triển "
        "thêm tính năng và ký kết phụ lục khi cần thiết."
    )
    intent, signals = _classify_legal_contract_intent(prose)

    assert "interrogative" not in signals
    assert "risk_terms" not in signals
    assert intent == "UNSURE"


def test_a_yes_no_question_with_co_duoc_is_still_a_question():
    intent, signals = _classify_legal_contract_intent("Bên A có được phạt 30% không")

    assert signals["interrogative"] is True
    assert signals["risk_terms"] is True
    assert intent == "QUESTION"


def test_english_markers_match_whole_words_only():
    _, signals = _classify_legal_contract_intent(
        "Please show the standard onboarding checklist for new staff members"
    )

    # "show" is not "how", "standard" is not "nda", "checklist" is not "check".
    assert "interrogative" not in signals
    assert "explicit_request" not in signals


@pytest.mark.parametrize(
    "reply,confirmed",
    [
        # The first word is the answer, whatever follows it.
        ("ok, không cần hỏi thêm", True),
        ("có, không vấn đề gì", True),
        ("thôi được rồi, rà soát đi", True),
        ("ừ", True),
        ("vâng", True),
        ("đúng rồi bạn", True),
        ("không, chỉ hỏi thôi", False),
        ("đừng rà soát", False),
        ("thôi", False),
        ("thôi khỏi", False),
        ("tôi không muốn rà soát", False),
        ("công ty tôi", False),
    ],
)
def test_a_reply_to_the_pending_offer_is_read_by_its_answer(reply, confirmed):
    from app.services.agents.agent_executor import (
        _is_review_confirmation,
        _is_review_decline,
    )

    assert _is_review_confirmation(reply) is confirmed
    if confirmed:
        assert _is_review_decline(reply) is False


# --- Excerpts are reviewed for what they say, not for what they lack ----------


def _missing_clause_findings(review_card):
    return [f for f in review_card["findings"] if f["finding_type"] == "MISSING_CLAUSE"]


def test_an_excerpt_the_model_recognises_is_not_faulted_for_missing_clauses(
    client, employee_token_headers, monkeypatch
):
    _route_with(monkeypatch, ScriptedRouter(NONE={"intent": "REVIEW", "scope": "EXCERPT"}))
    first = _chat(client, employee_token_headers, AMBIGUOUS)
    assert first["legal_risk_card"]["document_scope"] == "EXCERPT"
    monkeypatch.undo()

    reviewed = _chat(client, employee_token_headers, "Bên B", first["conversation_id"])

    card = reviewed["legal_risk_card"]
    assert card["document_scope"] == "EXCERPT"
    assert _missing_clause_findings(card) == []
    assert {item["status"] for item in card["checklist"]} <= {"PRESENT", "NOT_IN_EXCERPT"}
    # What the excerpt does say is still reviewed.
    assert any(f["category"] == "PENALTY" for f in card["findings"])
    assert "đoạn trích" in reviewed["reply"]


def test_the_scope_survives_the_are_you_sure_question(client, employee_token_headers):
    """Without a model: an unstructured snippet is an excerpt, confirmed two turns later."""
    first = _chat(client, employee_token_headers, AMBIGUOUS)
    assert first["legal_risk_card"]["document_scope"] == "EXCERPT"
    cid = first["conversation_id"]

    confirmed = _chat(client, employee_token_headers, "rà soát", cid)
    assert confirmed["legal_risk_card"]["document_scope"] == "EXCERPT"
    reviewed = _chat(client, employee_token_headers, "Bên B", cid)

    assert _missing_clause_findings(reviewed["legal_risk_card"]) == []


def test_a_whole_contract_is_still_faulted_for_missing_clauses(
    client, employee_token_headers, monkeypatch
):
    _route_with(monkeypatch, ScriptedRouter(NONE={"intent": "REVIEW", "scope": "FULL"}))
    first = _chat(client, employee_token_headers, SHORT_CLAUSE)
    monkeypatch.undo()

    reviewed = _chat(client, employee_token_headers, "Bên A", first["conversation_id"])

    card = reviewed["legal_risk_card"]
    assert card["document_scope"] == "FULL"
    assert _missing_clause_findings(card)
    assert "đoạn trích" not in reviewed["reply"]


# --- Cancelling is read by the model; the keywords are only its fallback --------


class PerspectiveRouter:
    """Answers the perspective question with one fixed reading."""

    enabled = True

    def __init__(self, party, decision):
        self.reply = {"represented_party": party, "decision": decision}

    def generate_text(self, _messages, **_kwargs):
        import json

        return {"provider": "gemini", "content": json.dumps(self.reply)}


SKIP_A_SECTION = "Tôi là bên A, bỏ qua phần bảo mật nhé"


def test_skipping_a_section_while_naming_a_side_is_not_a_cancellation(
    client, employee_token_headers, monkeypatch
):
    """"bỏ qua" used to cancel the review before the model was even asked."""
    first = _chat(client, employee_token_headers, SHORT_CLAUSE)
    assert first["legal_risk_card"]["status"] == "COLLECTING"
    _route_with(monkeypatch, PerspectiveRouter("PARTY_A", "ANSWER"))

    reply = _chat(client, employee_token_headers, SKIP_A_SECTION, first["conversation_id"])

    assert "audit_contract_risk" in _tool_names(reply)
    assert reply["legal_risk_card"]["represented_party"] == "PARTY_A"


def test_the_cancel_keywords_still_apply_when_no_model_answers(
    client, employee_token_headers
):
    first = _chat(client, employee_token_headers, SHORT_CLAUSE)

    reply = _chat(client, employee_token_headers, "hủy rà soát", first["conversation_id"])

    assert reply["legal_risk_card"]["status"] == "CANCELLED"


def test_a_model_cancellation_is_honoured(client, employee_token_headers, monkeypatch):
    first = _chat(client, employee_token_headers, SHORT_CLAUSE)
    _route_with(monkeypatch, PerspectiveRouter(None, "CANCEL"))

    reply = _chat(client, employee_token_headers, "thôi để sau", first["conversation_id"])

    assert reply["legal_risk_card"]["status"] == "CANCELLED"
    assert "audit_contract_risk" not in _tool_names(reply)
