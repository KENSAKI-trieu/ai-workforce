"""A finished contract review must not keep answering the turns that follow it.

The perspective question is slot-filling held in chat history: the agent finds the
open CONTRACT_REVIEW_DRAFT card and resumes it. The finished review is stored under
the same attachment type but carries the analyzer output, which has no "type" of its
own, so the scan used to step straight past it and pick up the COLLECTING card from
before the review. Everything after a completed review was then answered with "Tôi
chưa xác định được bạn đại diện bên nào" -- and any later message that happened to
name a party silently re-ran the audit on the old contract and opened a second
approval for it.
"""

from app.models.models import ContractReview, WorkflowApproval
from tests.test_legal_chat_perspective import CONTRACT, _chat, _tool_names


AMBIGUOUS = (
    "Căn cứ Bộ luật Dân sự, các bên thỏa thuận mức phạt vi phạm "
    "là 30% giá trị hợp đồng đã ký."
)


def _reviewed(client, headers):
    """Run a contract through to a finished review and return that conversation."""
    opened = _chat(client, headers, CONTRACT)
    reviewed = _chat(client, headers, "Bên A", opened["conversation_id"])
    assert reviewed["legal_risk_card"]["total_risks_found"] > 0
    return opened["conversation_id"], reviewed


def test_a_question_after_a_review_is_answered_not_re_asked(client, employee_token_headers):
    conversation_id, _ = _reviewed(client, employee_token_headers)

    follow_up = _chat(
        client,
        employee_token_headers,
        "Điều khoản phạt 30% có hợp lệ không?",
        conversation_id,
    )

    assert "đại diện" not in follow_up["reply"]
    assert "Bên A" not in follow_up["reply"]
    assert "audit_contract_risk" not in _tool_names(follow_up)
    # The turn reached the normal question path instead of the slot-filling loop.
    assert _tool_names(follow_up)[0] == "rag_search"


def test_naming_a_party_after_a_review_does_not_re_audit_the_old_contract(
    client, employee_token_headers, transactional_db_session
):
    conversation_id, reviewed = _reviewed(client, employee_token_headers)
    before = transactional_db_session.query(WorkflowApproval).filter(
        WorkflowApproval.action_type == "LEGAL_CONTRACT_APPROVAL"
    ).count()

    follow_up = _chat(
        client,
        employee_token_headers,
        "Nếu tôi là khách hàng thì sao?",
        conversation_id,
    )

    assert "audit_contract_risk" not in _tool_names(follow_up)
    assert follow_up.get("legal_risk_card") is None
    after = transactional_db_session.query(WorkflowApproval).filter(
        WorkflowApproval.action_type == "LEGAL_CONTRACT_APPROVAL"
    ).count()
    assert after == before


def test_a_new_contract_after_a_review_still_starts_a_fresh_review(
    client, employee_token_headers
):
    """The fix must close the finished review, not deafen the agent to new ones."""
    conversation_id, _ = _reviewed(client, employee_token_headers)

    again = _chat(client, employee_token_headers, CONTRACT, conversation_id)

    card = again["legal_risk_card"]
    assert card["type"] == "CONTRACT_REVIEW_DRAFT"
    assert card["status"] == "COLLECTING"
    answered = _chat(client, employee_token_headers, "Bên B", conversation_id)
    assert answered["legal_risk_card"]["represented_party"] == "PARTY_B"


def test_declining_the_ambiguous_prompt_does_not_start_a_review(
    client, employee_token_headers
):
    """"Đừng rà soát" used to read as a yes: the confirm markers were substrings.

    Tone marks are stripped before matching, which collapses "đừng" onto "đúng", and
    the bare marker "co" matched inside "công ty".
    """
    opened = _chat(client, employee_token_headers, AMBIGUOUS)
    assert opened["legal_risk_card"]["status"] == "AWAITING_INTENT"

    declined = _chat(
        client, employee_token_headers, "Đừng rà soát, tôi chỉ hỏi thôi", opened["conversation_id"]
    )

    assert "đại diện cho bên nào" not in declined["reply"]
    assert "audit_contract_risk" not in _tool_names(declined)
    # The open question is closed, so the next turn is read on its own terms.
    assert declined["legal_risk_card"]["status"] == "DISMISSED"
    later = _chat(
        client, employee_token_headers, "Indemnification là gì?", opened["conversation_id"]
    )
    assert "đại diện cho bên nào" not in later["reply"]


def test_confirming_the_ambiguous_prompt_still_asks_for_the_perspective(
    client, employee_token_headers
):
    opened = _chat(client, employee_token_headers, AMBIGUOUS)
    confirmed = _chat(
        client, employee_token_headers, "rà soát giúp tôi", opened["conversation_id"]
    )

    assert confirmed["legal_risk_card"]["status"] == "COLLECTING"
    assert "đại diện cho bên nào" in confirmed["reply"]


def test_reviewing_the_same_contract_twice_reuses_the_review_and_sends_nothing(
    client, employee_token_headers, transactional_db_session
):
    """One contract, one review -- and no approval card until the reviewer sends it.

    Each run used to open an approval of its own before the reviewer had looked at
    the findings.
    """
    _, first = _reviewed(client, employee_token_headers)
    _, second = _reviewed(client, employee_token_headers)

    assert first["legal_risk_card"]["review_id"] == second["legal_risk_card"]["review_id"]

    review_id = first["legal_risk_card"]["review_id"]
    review = transactional_db_session.query(ContractReview).filter(
        ContractReview.id == review_id
    ).one()
    assert review.workflow_id is None
    sent = [
        approval
        for approval in transactional_db_session.query(WorkflowApproval).all()
        if (approval.payload or {}).get("contract_review_id") == review_id
    ]
    assert sent == []


def test_a_chat_review_reopens_unsent_and_ready_to_send(client, employee_token_headers):
    """The saved review is what the chat card links to; it is sent from there."""
    _, reviewed = _reviewed(client, employee_token_headers)
    review_id = reviewed["legal_risk_card"]["review_id"]

    response = client.get(
        f"/api/v1/legal/contract-reviews/{review_id}", headers=employee_token_headers
    )

    assert response.status_code == 200, response.text
    reopened = response.json()
    assert reopened["workflow_id"] is None
    assert reopened["approval"] is None
    assert reopened["requires_legal_approval"] is True
    assert reopened["total_risks_found"] == reviewed["legal_risk_card"]["total_risks_found"]
    assert reopened["findings"]
