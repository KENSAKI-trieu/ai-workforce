"""The HR agent on LangGraph: its capabilities as gateway tools.

Each tool runs the deterministic HR chat's own branch with the arguments the model chose.
The reply is written by the backend and, when it carries personal data, kept on the backend:
the graph only carries a reference, and the chat response gets the stored reply and card.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.agents import chat as chat_module
from app.agents.langgraph.engine import LangGraphEngine
from app.agents.tool_outputs import take_tool_output
from app.clients.ai_service_client import AIServiceError
from app.core.config import settings
from app.core.hr_capabilities import HR_GATEWAY_TOOLS, HR_PRIVATE_REPLY_TOOLS
from app.core.security import create_internal_tool_token
from app.domains.platform.auth_service import supported_agent_tools
from app.models.models import AgentToolOutput, AIAgent, ChatConversation, ChatMessage, User
from app.tools.executors.hr import PRIVATE_REPLY_PLACEHOLDER
from app.tools.registry import tool_registry


@pytest.fixture(scope="module")
def employee(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "employee@company.com").one()


def _conversation(db, user, *user_messages):
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id, AIAgent.role_code == "HR"
    ).one()
    conversation = ChatConversation(
        tenant_id=user.tenant_id, user_id=user.id, ai_agent_id=agent.id, title="hr"
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


def _invoke(client, user, tool, conversation_id, *, idempotent=False, **arguments):
    token = create_internal_tool_token(user, agent_role="HR")
    audit = {"correlation_id": str(uuid.uuid4()), "conversation_id": str(conversation_id)}
    if idempotent:
        audit["idempotency_key"] = f"test:{uuid.uuid4()}"
    response = client.post(
        f"/api/v1/internal/tools/{tool}/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"input": {"tenant_id": str(user.tenant_id), "audit": audit, **arguments}},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def _stored(db, user, conversation, result):
    return take_tool_output(
        db, user=user, conversation_id=str(conversation.id), output_id=result["output_id"]
    )


def test_every_hr_capability_is_a_terminal_gateway_tool() -> None:
    definitions = {item.name: item for item in tool_registry.all()}
    assert HR_GATEWAY_TOOLS <= set(definitions)
    for name in HR_GATEWAY_TOOLS:
        assert definitions[name].terminal, name
    # Writes the user's own request authorises run without a second approval; a leave
    # request opens its own.
    assert definitions["cancel_leave_request"].runs_on_request
    assert definitions["create_onboarding_workflow"].runs_on_request
    assert definitions["request_leave"].opens_approval
    assert definitions["cancel_leave_request"].public_metadata()["runs_on_request"] is True


def test_other_agents_are_not_offered_the_hr_tools() -> None:
    assert HR_GATEWAY_TOOLS <= supported_agent_tools("HR")
    assert not HR_GATEWAY_TOOLS & supported_agent_tools("LEGAL")
    assert not HR_GATEWAY_TOOLS & supported_agent_tools("FINANCE")


def test_a_private_reply_stays_on_the_backend(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, "Tôi còn bao nhiêu ngày phép?")

    result = _invoke(client, employee, "query_leave_balance", conversation.id)

    # The graph gets a stand-in, never the balance itself.
    assert "query_leave_balance" in HR_PRIVATE_REPLY_TOOLS
    assert result["reply"] == PRIVATE_REPLY_PLACEHOLDER
    stored = _stored(transactional_db_session, employee, conversation, result)
    assert "Còn lại" in stored["reply"]
    assert stored["hr_card"]["type"] == "LEAVE_BALANCE"
    # Read once: the row is gone.
    assert transactional_db_session.get(AgentToolOutput, uuid.UUID(result["output_id"])) is None


def test_a_stored_output_is_only_read_by_its_own_conversation(
    client, transactional_db_session, employee
):
    conversation = _conversation(transactional_db_session, employee, "Tôi còn bao nhiêu ngày phép?")
    other = _conversation(transactional_db_session, employee, "khác")
    result = _invoke(client, employee, "query_leave_balance", conversation.id)

    assert _stored(transactional_db_session, employee, other, result) is None
    assert _stored(transactional_db_session, employee, conversation, result) is not None


def test_a_colleagues_leave_balance_is_refused(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, "An còn mấy ngày phép?")

    result = _invoke(client, employee, "query_leave_balance", conversation.id, person="An")

    stored = _stored(transactional_db_session, employee, conversation, result)
    assert "chính bạn" in stored["reply"]
    assert "hr_card" not in stored


def test_department_names_in_the_users_words_filter_the_directory(
    client, transactional_db_session, employee
):
    conversation = _conversation(transactional_db_session, employee, "nhân viên bên kế toán")

    result = _invoke(
        client, employee, "query_company_users_sql", conversation.id, departments=["bên kế toán"]
    )

    stored = _stored(transactional_db_session, employee, conversation, result)
    assert stored["hr_card"]["type"] == "EMPLOYEE_SEARCH"
    assert stored["hr_card"]["department_filter"] == ["FINANCE"]


def test_a_department_the_company_lacks_is_not_dropped(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, "nhân viên phòng hậu cần")

    result = _invoke(
        client, employee, "query_company_users_sql", conversation.id, departments=["hậu cần"]
    )

    stored = _stored(transactional_db_session, employee, conversation, result)
    # Listing the whole company under "hậu cần" would be the silent wrong answer.
    assert "hr_card" not in stored
    assert "không tìm thấy phòng ban" in stored["reply"]


def test_a_masked_email_is_read_from_the_users_message(client, transactional_db_session):
    admin = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    conversation = _conversation(transactional_db_session, admin, "employee@company.com là ai?")

    result = _invoke(
        client, admin, "query_company_users_sql", conversation.id, person="[REDACTED_EMAIL]"
    )

    stored = _stored(transactional_db_session, admin, conversation, result)
    assert "hr_card" in stored, stored["reply"]
    assert stored["hr_card"]["type"] in {"EMPLOYEE_PROFILE", "EMPLOYEE_SEARCH"}
    assert "employee@company.com" in str(stored["hr_card"])


def test_a_leave_request_asks_for_what_is_missing(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, "Tôi muốn xin nghỉ")

    result = _invoke(
        client, employee, "request_leave", conversation.id, idempotent=True,
        reason="Việc gia đình",
    )

    # Not private: the model needs the question to carry on gathering the request.
    assert "ngày bắt đầu" in result["reply"]
    assert result["created"] is False
    stored = _stored(transactional_db_session, employee, conversation, result)
    assert stored["hr_card"]["type"] == "LEAVE_REQUEST_DRAFT"
    assert stored["hr_card"]["status"] == "COLLECTING"
    assert stored["hr_card"]["reason"] == "Việc gia đình"


def test_a_dropped_leave_request_files_nothing(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, "thôi không xin nữa")

    result = _invoke(client, employee, "request_leave", conversation.id, idempotent=True, abandon=True)

    stored = _stored(transactional_db_session, employee, conversation, result)
    assert "hủy bản nháp" in stored["reply"]
    assert stored["hr_card"]["status"] == "CANCELLED"
    assert "approval_card" not in stored


def test_a_complete_leave_request_goes_to_approval(client, transactional_db_session, employee):
    start = date.today() + timedelta(days=40)
    conversation = _conversation(transactional_db_session, employee, "xin nghỉ")

    result = _invoke(
        client, employee, "request_leave", conversation.id, idempotent=True,
        start_date=start.isoformat(), end_date=start.isoformat(), reason="Khám bệnh",
    )

    stored = _stored(transactional_db_session, employee, conversation, result)
    assert result["created"] is True, stored["reply"]
    assert stored["approval_card"]["status"] == "WAITING"


def test_a_deep_profile_needs_a_stated_purpose(client, transactional_db_session, employee):
    conversation = _conversation(
        transactional_db_session, employee, "Cho tôi hồ sơ admin@company.com", "để làm gì à?"
    )

    result = _invoke(
        client, employee, "get_employee_full_profile", conversation.id, employee="OTHER"
    )

    stored = _stored(transactional_db_session, employee, conversation, result)
    assert "mục đích" in stored["reply"]
    assert "hr_card" not in stored


def test_onboarding_by_a_non_hr_user_is_refused_in_words(client, transactional_db_session, employee):
    conversation = _conversation(transactional_db_session, employee, "Tạo onboarding cho new@company.com")

    result = _invoke(client, employee, "create_onboarding_workflow", conversation.id, idempotent=True)

    stored = _stored(transactional_db_session, employee, conversation, result)
    assert "không có quyền" in stored["reply"]


def test_the_chat_response_shows_the_stored_reply_and_card(transactional_db_session, employee):
    db = transactional_db_session
    conversation = _conversation(db, employee, "phép của tôi")
    output = AgentToolOutput(
        tenant_id=employee.tenant_id,
        user_id=employee.id,
        conversation_id=conversation.id,
        tool_name="query_leave_balance",
        reply="Bạn còn **9 ngày** phép. Liên hệ hr@company.com.",
        cards={"hr_card": {"type": "LEAVE_BALANCE"}},
    )
    db.add(output)
    db.flush()
    result = {
        "status": "COMPLETED",
        "state": {
            # The graph's copy: masked and a stand-in.
            "final_answer": PRIVATE_REPLY_PLACEHOLDER,
            "tool_calls": [{
                "name": "query_leave_balance",
                "status": "SUCCESS",
                "terminal": True,
                "result": {"output_id": str(output.id), "reply": PRIVATE_REPLY_PLACEHOLDER},
            }],
        },
    }
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id, AIAgent.role_code == "HR"
    ).one()

    stored = LangGraphEngine.stored_tool_output(db, employee, result, str(conversation.id))
    response = LangGraphEngine.to_chat_response(
        result, agent, workflow=type("W", (), {"id": uuid.uuid4()})(), approval=None,
        tool_output=stored,
    )

    assert response["reply"] == "Bạn còn **9 ngày** phép. Liên hệ hr@company.com."
    assert response["hr_card"] == {"type": "LEAVE_BALANCE"}


def test_a_private_reply_is_not_handed_back_to_the_model_as_history(
    transactional_db_session, employee
):
    db = transactional_db_session
    conversation = _conversation(db, employee, "lương của tôi")
    db.add(ChatMessage(
        conversation_id=conversation.id,
        sender="ASSISTANT",
        content="Lương cơ bản của bạn là 30.000.000 ₫.",
        tools_executed=[{"tool_name": "get_employee_compensation_summary", "status": "SUCCESS"}],
        created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ))
    db.flush()

    history = LangGraphEngine._history(db, str(conversation.id), "câu mới")

    assistant = [item["content"] for item in history if item["role"] == "assistant"]
    assert assistant and "30.000.000" not in assistant[0]
    assert "xem thông tin lương" in assistant[0]


def test_hr_chat_runs_on_the_graph_and_falls_back_to_its_gate(
    monkeypatch, transactional_db_session, employee
):
    monkeypatch.setattr(settings, "AGENT_ENGINES", "HR=langgraph")
    monkeypatch.setattr(settings, "LANGGRAPH_LEGACY_FALLBACK", True)
    calls: list[str] = []

    class FailingGraph:
        def execute(self, **_kwargs):
            calls.append("graph")
            raise AIServiceError("down", status_code=503)

    def gate(*_args, **_kwargs):
        calls.append("gate")
        return {"reply": "từ luồng HR"}

    monkeypatch.setattr(chat_module, "LangGraphEngine", FailingGraph)
    monkeypatch.setattr(chat_module, "_run_hr_gate", gate)

    response = chat_module.execute_agent_chat(
        transactional_db_session, employee, "HR", "phép của tôi", thread_id=None
    )

    assert calls == ["graph", "gate"]
    assert response["reply"] == "từ luồng HR"
