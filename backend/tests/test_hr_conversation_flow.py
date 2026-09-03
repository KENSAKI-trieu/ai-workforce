"""Multi-turn HR conversations and the SSE contract, exercised through the real API.

The unit tests around the router cover single decisions in isolation. These drive whole
conversations, where the failures live: an open leave draft changing how the next turn is
read, a grant withheld halfway down a branch, a refusal raised inside the SSE generator.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.models import AgentWorkflow, AIAgent, User, WorkflowApproval
from app.services.agents import agent_executor


def chat(client, headers, message, conversation_id=None):
    payload = {"agent_role": "HR", "message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/v1/agent/chat", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def tools(data):
    return [item["tool_name"] for item in data["tools_executed"]]


def card_type(data):
    return (data.get("hr_card") or {}).get("type")


def hr_agent_for(db, email):
    actor = db.query(User).filter(User.email == email).one()
    return db.query(AIAgent).filter(
        AIAgent.tenant_id == actor.tenant_id,
        AIAgent.role_code == "HR",
    ).one()


# ---------------------------------------------------------------- branch coverage


@pytest.mark.parametrize(
    ("message", "expected_card", "expected_tool"),
    [
        ("Tôi còn bao nhiêu ngày phép?", "LEAVE_BALANCE", "query_leave_balance"),
        ("Hồ sơ của tôi", "EMPLOYEE_PROFILE", "get_employee_full_profile"),
        ("lương của tôi bao nhiêu", "EMPLOYEE_PROFILE", "get_employee_compensation_summary"),
        ("hợp đồng của tôi hết hạn khi nào", "EMPLOYEE_PROFILE", "get_employee_contract_summary"),
        ("thông tin cá nhân của tôi", "EMPLOYEE_PROFILE", "get_employee_private_profile"),
        ("Danh sách nhân viên", "EMPLOYEE_SEARCH", "query_company_users_sql"),
        ("danh sách quản lý", "EMPLOYEE_SEARCH", "query_company_users_sql"),
    ],
)
def test_read_intents_produce_their_card_and_tool(
    client, employee_token_headers, message, expected_card, expected_tool
):
    data = chat(client, employee_token_headers, message)
    assert card_type(data) == expected_card, data["reply"]
    assert expected_tool in tools(data), tools(data)
    assert data["reply"].strip()


def test_profile_branches_record_what_the_policy_engine_released(
    client, employee_token_headers
):
    """The trace carries the allowed sections, not a constant restating the tool name."""
    data = chat(client, employee_token_headers, "lương của tôi bao nhiêu")
    entry = next(
        item for item in data["tools_executed"]
        if item["tool_name"] == "get_employee_compensation_summary"
    )
    assert set(entry["result"]) == {"allowed_sections", "denied_sections", "masked_fields"}
    assert "COMPENSATION" in entry["result"]["allowed_sections"]


def test_leave_statistics_states_the_gap_then_searches(client, ceo_token_headers):
    data = chat(client, ceo_token_headers, "có bao nhiêu nhân viên đang nghỉ phép")
    assert "chưa có tool" in data["reply"].lower()
    assert tools(data) == ["hybrid_rag_search"]
    # The caveat must survive verbatim — it is the honest part of the answer.
    assert data["reply"].startswith("HR Agent chưa có tool")


@pytest.mark.parametrize(
    "message",
    ["An còn bao nhiêu ngày phép?", "quỹ phép của An"],
)
def test_leave_balance_about_a_colleague_is_refused(
    client, employee_token_headers, message
):
    data = chat(client, employee_token_headers, message)
    assert tools(data) == []
    assert data["hr_card"] is None
    assert "chính bạn" in data["reply"]


def test_self_profile_drops_leave_when_that_grant_is_withheld(
    client, employee_token_headers, transactional_db_session
):
    """The leave summary is its own grant; withholding it narrows the answer, not fails it."""
    data = chat(client, employee_token_headers, "Hồ sơ của tôi")
    assert "LEAVE" in data["hr_card"]["access"]["allowed_sections"]

    agent = hr_agent_for(transactional_db_session, "employee@company.com")
    original = list(agent.disallowed_actions or [])
    agent.disallowed_actions = sorted(set(original) | {"get_employee_leave_summary"})
    transactional_db_session.commit()
    try:
        narrowed = chat(client, employee_token_headers, "Hồ sơ của tôi")
        assert card_type(narrowed) == "EMPLOYEE_PROFILE"
        assert "LEAVE" not in narrowed["hr_card"]["access"]["allowed_sections"]
    finally:
        agent.disallowed_actions = original
        transactional_db_session.commit()


# ---------------------------------------------------------------- pending approvals


def _waiting_approvals(db, actor: User, count: int) -> None:
    """Replace the tenant's WAITING queue with exactly `count` rows the CEO can approve."""
    tenant_workflows = db.query(AgentWorkflow.id).filter(
        AgentWorkflow.tenant_id == actor.tenant_id
    ).subquery()
    db.query(WorkflowApproval).filter(
        WorkflowApproval.status == "WAITING",
        WorkflowApproval.workflow_id.in_(db.query(tenant_workflows.c.id)),
    ).delete(synchronize_session=False)
    for index in range(count):
        workflow = AgentWorkflow(
            id=uuid.uuid4(),
            tenant_id=actor.tenant_id,
            initiator_id=actor.id,
            title=f"Yêu cầu {index}",
            status="AWAITING_APPROVAL",
        )
        db.add(workflow)
        db.flush()
        db.add(WorkflowApproval(
            id=uuid.uuid4(),
            workflow_id=workflow.id,
            approver_id=actor.id,
            action_type="LEAVE_REQUEST",
            risk_level="LOW",
            payload={"requester_id": str(actor.id)},
            status="WAITING",
        ))
    db.commit()


def test_pending_approvals_reports_a_bounded_count(client, ceo_token_headers):
    data = chat(client, ceo_token_headers, "đơn chờ duyệt")
    assert card_type(data) == "PENDING_APPROVALS"
    assert data["hr_card"]["truncated"] is False
    assert len(data["hr_card"]["items"]) <= 20


def test_a_queue_that_exactly_fills_the_scan_window_is_not_truncated(
    client, ceo_token_headers, transactional_db_session, monkeypatch
):
    """Exhausting the window is not the same as leaving rows behind."""
    actor = transactional_db_session.query(User).filter(
        User.email == "admin@company.com"
    ).one()
    _waiting_approvals(transactional_db_session, actor, 3)
    monkeypatch.setattr(agent_executor, "PENDING_APPROVAL_BATCH", 1)
    monkeypatch.setattr(agent_executor, "PENDING_APPROVAL_SCAN_LIMIT", 3)

    data = chat(client, ceo_token_headers, "đơn chờ duyệt")

    assert data["hr_card"]["truncated"] is False, data["reply"]
    assert "ít nhất" not in data["reply"]


def test_a_queue_longer_than_the_scan_window_is_reported_as_truncated(
    client, ceo_token_headers, transactional_db_session, monkeypatch
):
    actor = transactional_db_session.query(User).filter(
        User.email == "admin@company.com"
    ).one()
    _waiting_approvals(transactional_db_session, actor, 3)
    monkeypatch.setattr(agent_executor, "PENDING_APPROVAL_BATCH", 1)
    monkeypatch.setattr(agent_executor, "PENDING_APPROVAL_SCAN_LIMIT", 2)

    data = chat(client, ceo_token_headers, "đơn chờ duyệt")

    assert data["hr_card"]["truncated"] is True, data["reply"]
    assert "ít nhất" in data["reply"]


# ---------------------------------------------------------------- export


def test_export_asks_for_missing_slots_then_produces_a_download(client, ceo_token_headers):
    incomplete = chat(client, ceo_token_headers, "Xuất file")
    assert incomplete["tools_executed"] == []
    assert "loại dữ liệu" in incomplete["reply"]

    ready = chat(client, ceo_token_headers, "Xuất danh sách nhân viên Excel")
    assert card_type(ready) == "FILE_EXPORT"
    assert ready["hr_card"]["format"] == "xlsx"
    assert "export_hr_directory" in tools(ready)
    # The branch issues a link; the dataset is read and audited by the export endpoint.
    entry = next(item for item in ready["tools_executed"] if item["tool_name"] == "export_hr_directory")
    assert entry["result"] == {"download_link_issued": True}


# ---------------------------------------------------------------- leave conversation


def test_leave_request_collects_slots_across_turns_then_submits(
    client, employee_token_headers
):
    first = chat(client, employee_token_headers, "tôi muốn xin nghỉ phép")
    conversation_id = first["conversation_id"]
    assert card_type(first) == "LEAVE_REQUEST_DRAFT"
    assert set(first["hr_card"]["missing_fields"]) == {"start_date", "end_date", "reason"}

    dated = chat(client, employee_token_headers, "Từ 20/12 đến 22/12", conversation_id)
    assert card_type(dated) == "LEAVE_REQUEST_DRAFT"
    assert dated["hr_card"]["start_date"] is not None
    assert dated["hr_card"]["end_date"] is not None
    assert dated["hr_card"]["missing_fields"] == ["reason"]

    submitted = chat(client, employee_token_headers, "về quê ăn cưới", conversation_id)
    assert submitted["approval_card"] is not None, submitted["reply"]
    assert "request_leave" in tools(submitted)


def test_a_policy_question_mid_draft_is_answered_and_the_draft_survives(
    client, employee_token_headers
):
    """A date in a question is not consent to submit one."""
    first = chat(client, employee_token_headers, "tôi muốn xin nghỉ phép")
    conversation_id = first["conversation_id"]

    question = chat(
        client,
        employee_token_headers,
        "Nghỉ ngày 20/12 có bị trừ lương không?",
        conversation_id,
    )
    assert card_type(question) != "LEAVE_REQUEST_DRAFT", question["reply"]
    assert "bổ sung" not in question["reply"]

    resumed = chat(client, employee_token_headers, "Từ 20/12 đến 22/12", conversation_id)
    assert card_type(resumed) == "LEAVE_REQUEST_DRAFT"
    assert resumed["hr_card"]["start_date"] is not None


@pytest.mark.parametrize(
    "question",
    [
        "Nghỉ phép có cần đơn không",
        "Điều kiện nghỉ phép năm",
        "Nghỉ phép được mấy ngày",
    ],
)
def test_policy_questions_are_never_recorded_as_the_leave_reason(
    client, employee_token_headers, question
):
    first = chat(client, employee_token_headers, "tôi muốn xin nghỉ phép")
    conversation_id = first["conversation_id"]
    chat(client, employee_token_headers, "Từ 20/12 đến 22/12", conversation_id)

    result = chat(client, employee_token_headers, question, conversation_id)
    assert result["approval_card"] is None, result["reply"]
    assert card_type(result) != "LEAVE_REQUEST_DRAFT", result["reply"]


@pytest.mark.parametrize(
    "cancel_phrase",
    ["hủy", "Hủy bỏ", "thôi hủy đơn", "thôi không nghỉ nữa"],
)
def test_cancellations_never_become_the_reason(
    client, employee_token_headers, cancel_phrase
):
    """A bare "hủy" is a cancellation, not a reason to submit the request with."""
    first = chat(client, employee_token_headers, "tôi muốn xin nghỉ phép")
    conversation_id = first["conversation_id"]
    chat(client, employee_token_headers, "Từ 20/12 đến 22/12", conversation_id)

    result = chat(client, employee_token_headers, cancel_phrase, conversation_id)
    assert result["approval_card"] is None, result["reply"]
    assert card_type(result) == "LEAVE_REQUEST_DRAFT"
    assert result["hr_card"]["status"] == "CANCELLED", result["reply"]
    assert "Chưa có đơn nào" in result["reply"]


def test_cancelling_an_empty_draft_creates_nothing(client, employee_token_headers):
    """Cancelling before any slot is filled must not fall through to a submission."""
    first = chat(client, employee_token_headers, "tôi muốn xin nghỉ phép")
    conversation_id = first["conversation_id"]

    cancelled = chat(client, employee_token_headers, "thôi hủy đơn", conversation_id)
    assert cancelled["hr_card"]["status"] == "CANCELLED"
    assert cancelled["approval_card"] is None
    assert "Chưa có đơn nào" in cancelled["reply"]


def test_an_end_date_before_the_start_date_is_rejected(client, employee_token_headers):
    first = chat(client, employee_token_headers, "tôi muốn xin nghỉ phép")
    conversation_id = first["conversation_id"]
    chat(client, employee_token_headers, "Từ 22/12 đến 20/12", conversation_id)

    result = chat(client, employee_token_headers, "về quê", conversation_id)
    assert result["approval_card"] is None
    assert "kết thúc" in result["reply"].lower()


def test_a_rejected_date_pair_can_be_corrected_with_a_bare_date(
    client, employee_token_headers
):
    """Both slots are filled after a rejection, so the correction needs its own path.

    The AI service is disabled in tests, which is also how a local deployment runs, so
    this exercises the deterministic parser rather than the LLM slot extractor.
    """
    # November, so the dates cannot overlap the December request an earlier test in this
    # module submits: the session is rolled back per module, not per test.
    first = chat(client, employee_token_headers, "tôi muốn xin nghỉ phép")
    conversation_id = first["conversation_id"]
    chat(client, employee_token_headers, "Từ 12/11 đến 10/11", conversation_id)
    rejected = chat(client, employee_token_headers, "về quê", conversation_id)
    assert rejected["approval_card"] is None
    assert "kết thúc" in rejected["reply"].lower()

    corrected = chat(client, employee_token_headers, "14/11", conversation_id)

    assert corrected["approval_card"] is not None, corrected["reply"]
    assert "request_leave" in tools(corrected)


# ---------------------------------------------------------------- streaming


def test_hr_stream_reports_a_phase_before_each_blocking_step(
    client, employee_token_headers
):
    response = client.post(
        "/api/v1/agent/chat/stream",
        headers=employee_token_headers,
        json={"agent_role": "HR", "message": "Tôi còn bao nhiêu ngày phép?"},
    )
    assert response.status_code == 200, response.text
    for phase in ("ANALYZING", "SEARCHING", "TOOL_CALLING", "COMPLETED"):
        assert '"phase": "%s"' % phase in response.text, phase
    assert "event: complete" in response.text


def test_hr_stream_reports_a_permission_refusal_with_its_detail(
    client, employee_token_headers, transactional_db_session
):
    """A 403 is an answer, not a crash; the stream must not flatten it to a generic error."""
    agent = hr_agent_for(transactional_db_session, "employee@company.com")
    original = list(agent.disallowed_actions or [])
    agent.disallowed_actions = sorted(set(original) | {"query_leave_balance"})
    transactional_db_session.commit()
    try:
        response = client.post(
            "/api/v1/agent/chat/stream",
            headers=employee_token_headers,
            json={"agent_role": "HR", "message": "Tôi còn bao nhiêu ngày phép?"},
        )
        assert response.status_code == 200, response.text
        assert "event: error" in response.text
        assert "query_leave_balance" in response.text
    finally:
        agent.disallowed_actions = original
        transactional_db_session.commit()
