from __future__ import annotations

import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.agents.chat import execute_agent_chat
from app.agents.hr.intent import _classify_hr_intent
from tests.chat_patching import patch_chat
from app.agents.hr.llm_flow import (
    HRRequestClassification,
    classify_hr_request,
    HR_INTENT_LABELS,
    extract_leave_request_slots,
    generate_grounded_hr_answer,
)

# The chat entry point reads the caller's tenant to resolve plugin prompts, so the
# user double needs one even though these tests never reach a database.
_TEST_TENANT_ID = uuid.uuid4()


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """These unit tests use fakes only and do not require the integration database."""
    yield


@pytest.fixture(autouse=True)
def no_plugin_overlay(monkeypatch):
    """Route these tests through the unmodified default prompts.

    ``execute_agent_chat`` looks up the tenant's installed plugins, but the db and user
    here are bare fakes with no session behind them. These tests are about routing, so
    the lookup is stubbed to the "nothing installed" answer; the overlay itself is
    covered in test_plugins.py.
    """
    patch_chat(monkeypatch, "resolve_prompt_overlay", lambda *_a, **_k: None)


class FakeAIClient:
    enabled = True

    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def generate_text(self, messages, **_kwargs):
        self.calls.append(messages)
        return self.responses.pop(0)


def test_classifier_sends_the_unchanged_raw_user_message_to_llm():
    raw = "Cho tôi biết quy định nghỉ phép?  "
    client = FakeAIClient({
        "provider": "openai",
        "content": '{"kind":"QUESTION"}',
    })

    result = classify_hr_request(
        raw,
        detailed_intent="POLICY_QUERY",
        client=client,  # type: ignore[arg-type]
    )

    assert result == HRRequestClassification("QUESTION", "llm")
    assert client.calls[0][-1] == {"role": "user", "content": raw}


def test_classifier_uses_safe_action_fallback_for_local_echo_provider():
    client = FakeAIClient({
        "provider": "local",
        "content": "Local provider received: tạo đơn nghỉ phép",
    })

    result = classify_hr_request(
        "Tạo đơn nghỉ phép",
        detailed_intent="ACTION_LEAVE_REQUEST",
        client=client,  # type: ignore[arg-type]
    )

    assert result == HRRequestClassification("ACTION", "fallback")


def test_leave_slot_extractor_resolves_relative_dates_from_supplied_context():
    client = FakeAIClient({
        "provider": "openai",
        "content": json.dumps({
            "start_date": "2026-08-28",
            "end_date": "2026-08-29",
            "reason": "đưa người nhà đi khám",
        }, ensure_ascii=False),
    })

    result = extract_leave_request_slots(
        "Tôi xin nghỉ từ ngày mai tới ngày kia để đưa người nhà đi khám",
        existing=None,
        reference_date=date(2026, 8, 27),
        timezone_name="Asia/Ho_Chi_Minh",
        client=client,  # type: ignore[arg-type]
    )

    assert result == {
        "start_date": "2026-08-28",
        "end_date": "2026-08-29",
        "reason": "đưa người nhà đi khám",
    }
    prompt = json.loads(client.calls[0][-1]["content"])
    assert prompt["reference_date"] == "2026-08-27"
    assert prompt["timezone"] == "Asia/Ho_Chi_Minh"
    assert prompt["existing_draft"] == {
        "start_date": None,
        "end_date": None,
        "reason": None,
    }


def test_leave_slot_extractor_uses_draft_context_and_rejects_invalid_dates():
    client = FakeAIClient({
        "provider": "openai",
        "content": '{"start_date":null,"end_date":"29/08/2026","reason":null}',
    })

    result = extract_leave_request_slots(
        "đến ngày kia",
        existing={
            "start_date": "2026-08-28",
            "end_date": None,
            "reason": "việc gia đình",
        },
        reference_date=date(2026, 8, 27),
        timezone_name="Asia/Ho_Chi_Minh",
        client=client,  # type: ignore[arg-type]
    )

    assert result == {"start_date": None, "end_date": None, "reason": None}
    prompt = json.loads(client.calls[0][-1]["content"])
    assert prompt["missing_fields"] == ["end_date"]


def test_leave_slot_extractor_falls_back_for_local_echo_provider():
    client = FakeAIClient({
        "provider": "local",
        "content": "Local provider received: ngày mai",
    })

    result = extract_leave_request_slots(
        "ngày mai",
        existing=None,
        reference_date=date(2026, 8, 27),
        timezone_name="Asia/Ho_Chi_Minh",
        client=client,  # type: ignore[arg-type]
    )

    assert result == {"start_date": None, "end_date": None, "reason": None}


def test_question_answer_is_generated_from_governed_evidence_and_keeps_citation():
    citation = "[Citation: HR policy, p. 2]"
    client = FakeAIClient({
        "provider": "openai",
        "content": "Nhân viên có 12 ngày phép mỗi năm.",
    })
    response = {
        "reply": "12 ngày phép.",
        "citations": [{
            "id": "chunk-1",
            "content": "Nhân viên có 12 ngày phép mỗi năm.",
            "citation_tag": citation,
        }],
        "tools_executed": [{"tool_name": "rag_search", "result_count": 1}],
        "hr_card": None,
    }

    result = generate_grounded_hr_answer(
        "Tôi có bao nhiêu ngày phép?",
        response,
        client=client,  # type: ignore[arg-type]
    )

    assert result["reply"].endswith(citation)
    prompt = json.loads(client.calls[0][-1]["content"])
    assert prompt["question"] == "Tôi có bao nhiêu ngày phép?"
    assert prompt["governed_evidence"]["citations"][0]["id"] == "chunk-1"


def test_llm_question_classification_cannot_execute_an_action(monkeypatch):
    captured = {}
    patch_chat(monkeypatch, "_classify_hr_intent",
        lambda _message: "ACTION_LEAVE_REQUEST",
    )
    patch_chat(monkeypatch, "_load_leave_draft", lambda *_args: None)
    patch_chat(monkeypatch, "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification("QUESTION", "llm"),
    )

    def fake_core(*_args, **kwargs):
        captured["intent"] = kwargs["hr_intent_override"]
        return {"reply": "retrieved", "citations": [], "tools_executed": [], "hr_card": None}

    patch_chat(monkeypatch, "_execute_agent_chat_core", fake_core)
    patch_chat(monkeypatch, "generate_grounded_hr_answer",
        lambda _message, response, **_kwargs: {**response, "generated": True},
    )

    result = execute_agent_chat(
        SimpleNamespace(),
        SimpleNamespace(tenant_id=_TEST_TENANT_ID),
        "HR",
        "Tôi có thể xin nghỉ ngày mai không?",
    )

    assert captured["intent"] == "POLICY_QUERY"
    assert result["generated"] is True


def test_classifier_returns_the_detailed_intent_label_alongside_the_kind():
    client = FakeAIClient({
        "provider": "openai",
        "content": '{"kind":"QUESTION","intent":"MANAGER_DIRECTORY"}',
    })

    result = classify_hr_request(
        "cho mình xem ai đang làm quản lý ở đây",
        detailed_intent="UNKNOWN",
        client=client,  # type: ignore[arg-type]
    )

    assert result == HRRequestClassification("QUESTION", "llm", "MANAGER_DIRECTORY")


def test_classifier_drops_an_intent_label_outside_the_closed_set():
    client = FakeAIClient({
        "provider": "openai",
        "content": '{"kind":"QUESTION","intent":"DELETE_ALL_EMPLOYEES"}',
    })

    result = classify_hr_request(
        "xoá hết nhân viên đi",
        detailed_intent="UNKNOWN",
        client=client,  # type: ignore[arg-type]
    )

    assert result == HRRequestClassification("QUESTION", "llm", None)


def test_every_executor_branch_label_is_offered_to_the_router():

    produced = {
        _classify_hr_intent(message)
        for message in (
            "còn bao nhiêu ngày phép",
            "xuất danh sách nhân viên excel",
            "tạo onboarding cho a@b.com",
            "hồ sơ đầy đủ của a@b.com",
            "lương của tôi",
            "thông tin cá nhân của tôi",
            "hồ sơ của tôi",
            "hợp đồng của tôi",
            "hợp đồng sắp hết hạn",
            "đơn chờ duyệt",
            "có bao nhiêu nhân viên đang nghỉ phép",
            "danh sách quản lý",
            "danh sách nhân viên",
            "tìm nhân viên An",
            "quy định nghỉ phép là gì",
            "tôi muốn xin nghỉ phép",
            "xin chào",
        )
    }
    assert produced <= HR_INTENT_LABELS


def test_router_intent_replaces_the_keyword_label_for_a_paraphrased_question(monkeypatch):
    captured = {}
    patch_chat(monkeypatch, "_classify_hr_intent", lambda _message: "UNKNOWN")
    patch_chat(monkeypatch, "_load_leave_draft", lambda *_args: None)
    patch_chat(monkeypatch, "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification(
            "QUESTION", "llm", "EMPLOYEE_DIRECTORY"
        ),
    )

    def fake_core(*_args, **kwargs):
        captured["intent"] = kwargs["hr_intent_override"]
        return {"reply": "", "citations": [], "tools_executed": [], "hr_card": None}

    patch_chat(monkeypatch, "_execute_agent_chat_core", fake_core)
    patch_chat(monkeypatch, "generate_grounded_hr_answer",
        lambda _message, response, **_kwargs: response,
    )

    execute_agent_chat(
        SimpleNamespace(),
        SimpleNamespace(tenant_id=_TEST_TENANT_ID),
        "HR",
        "công ty mình bao nhiêu người rồi",
    )

    assert captured["intent"] == "EMPLOYEE_DIRECTORY"


def test_router_action_with_a_read_only_intent_fails_closed(monkeypatch):
    captured = {}
    patch_chat(monkeypatch, "_classify_hr_intent", lambda _message: "UNKNOWN")
    patch_chat(monkeypatch, "_load_leave_draft", lambda *_args: None)
    patch_chat(monkeypatch, "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification(
            "ACTION", "llm", "EMPLOYEE_DIRECTORY"
        ),
    )

    def fake_core(*_args, **kwargs):
        captured["intent"] = kwargs["hr_intent_override"]
        return {"reply": "", "citations": [], "tools_executed": [], "hr_card": None}

    patch_chat(monkeypatch, "_execute_agent_chat_core", fake_core)

    execute_agent_chat(
        SimpleNamespace(),
        SimpleNamespace(tenant_id=_TEST_TENANT_ID),
        "HR",
        "làm gì đó với danh sách nhân viên",
    )

    assert captured["intent"] == "UNKNOWN"


def test_keyword_label_survives_when_the_router_falls_back(monkeypatch):
    captured = {}
    patch_chat(monkeypatch, "_classify_hr_intent", lambda _message: "QUERY_LEAVE_BALANCE"
    )
    patch_chat(monkeypatch, "_load_leave_draft", lambda *_args: None)
    patch_chat(monkeypatch, "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification("QUESTION", "fallback"),
    )

    def fake_core(*_args, **kwargs):
        captured["intent"] = kwargs["hr_intent_override"]
        return {"reply": "", "citations": [], "tools_executed": [], "hr_card": None}

    patch_chat(monkeypatch, "_execute_agent_chat_core", fake_core)
    patch_chat(monkeypatch, "generate_grounded_hr_answer",
        lambda _message, response, **_kwargs: response,
    )

    execute_agent_chat(
        SimpleNamespace(),
        SimpleNamespace(tenant_id=_TEST_TENANT_ID),
        "HR",
        "còn bao nhiêu ngày phép",
    )

    assert captured["intent"] == "QUERY_LEAVE_BALANCE"


def test_a_question_during_an_open_leave_draft_is_routed_by_the_router(monkeypatch):
    """H1: an open draft must not force every following turn to be an action."""
    captured = {}
    patch_chat(monkeypatch, "_classify_hr_intent", lambda _message: "POLICY_QUERY")
    patch_chat(monkeypatch, "_load_leave_draft",
        lambda *_args: {"type": "LEAVE_REQUEST_DRAFT", "missing_fields": ["reason"]},
    )
    patch_chat(monkeypatch, "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification("QUESTION", "llm", "POLICY_QUERY"),
    )

    def fake_core(*_args, **kwargs):
        captured["intent"] = kwargs["hr_intent_override"]
        captured["cancel"] = kwargs["leave_cancel_request"]
        return {"reply": "", "citations": [], "tools_executed": [], "hr_card": None}

    patch_chat(monkeypatch, "_execute_agent_chat_core", fake_core)
    patch_chat(monkeypatch, "generate_grounded_hr_answer",
        lambda _message, response, **_kwargs: response,
    )

    execute_agent_chat(
        SimpleNamespace(),
        SimpleNamespace(tenant_id=_TEST_TENANT_ID),
        "HR",
        "Nghỉ ngày 20/12 có bị trừ lương không?",
    )

    assert captured["intent"] == "POLICY_QUERY"
    assert captured["cancel"] is False


def test_cancelling_a_draft_is_never_reinterpreted_as_a_question(monkeypatch):
    """Cancellation is unambiguous, so the router does not get to override it."""
    captured = {}
    patch_chat(monkeypatch, "_classify_hr_intent", lambda _message: "UNKNOWN")
    patch_chat(monkeypatch, "_load_leave_draft",
        lambda *_args: {"type": "LEAVE_REQUEST_DRAFT", "missing_fields": ["reason"]},
    )
    patch_chat(monkeypatch, "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification("QUESTION", "llm", "POLICY_QUERY"),
    )

    def fake_core(*_args, **kwargs):
        captured["intent"] = kwargs["hr_intent_override"]
        captured["cancel"] = kwargs["leave_cancel_request"]
        return {"reply": "", "citations": [], "tools_executed": [], "hr_card": None}

    patch_chat(monkeypatch, "_execute_agent_chat_core", fake_core)

    execute_agent_chat(
        SimpleNamespace(),
        SimpleNamespace(tenant_id=_TEST_TENANT_ID),
        "HR",
        "Thôi hủy đơn giúp tôi",
    )

    assert captured["intent"] == "ACTION_LEAVE_REQUEST"
    assert captured["cancel"] is True


def test_personal_data_answers_are_never_sent_to_the_answer_model(monkeypatch):
    """H6: salary, contact details and deep profiles stay on the governed path."""
    calls = []
    patch_chat(monkeypatch, "_classify_hr_intent", lambda _message: "SELF_COMPENSATION")
    patch_chat(monkeypatch, "_load_leave_draft", lambda *_args: None)
    patch_chat(monkeypatch, "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification("QUESTION", "llm", "SELF_COMPENSATION"),
    )
    patch_chat(monkeypatch, "_execute_agent_chat_core",
        lambda *_args, **_kwargs: {
            "reply": "Lương tháng: 42.000.000 VND",
            "citations": [],
            "tools_executed": [],
            "hr_card": {"type": "EMPLOYEE_PROFILE", "compensation": {"monthly_salary": 42000000}},
        },
    )
    patch_chat(monkeypatch, "generate_grounded_hr_answer",
        lambda message, response: calls.append(message) or response,
    )

    result = execute_agent_chat(
        SimpleNamespace(), SimpleNamespace(tenant_id=_TEST_TENANT_ID), "HR", "lương của tôi là bao nhiêu"
    )

    assert calls == []
    assert result["reply"] == "Lương tháng: 42.000.000 VND"


def test_a_rewrite_that_drops_a_headline_figure_is_discarded():
    """H7: the answer model is told to keep figures; this enforces it."""
    response = {
        "reply": "Tôi tìm thấy 47 nhân viên trong phạm vi ALL.",
        "citations": [],
        "tools_executed": [{"tool_name": "query_company_users_sql", "result_count": 47}],
        "hr_card": {"type": "EMPLOYEE_SEARCH", "total_count": 47},
    }
    client = FakeAIClient({
        "provider": "openai",
        "content": "Công ty có khoảng 50 nhân viên.",
    })

    result = generate_grounded_hr_answer(
        "công ty có bao nhiêu nhân viên",
        response,
        client=client,  # type: ignore[arg-type]
    )

    assert result["reply"] == "Tôi tìm thấy 47 nhân viên trong phạm vi ALL."


def test_a_rewrite_that_keeps_every_headline_figure_is_accepted():
    response = {
        "reply": "Tôi tìm thấy 47 nhân viên trong phạm vi ALL.",
        "citations": [],
        "tools_executed": [{"tool_name": "query_company_users_sql", "result_count": 47}],
        "hr_card": {"type": "EMPLOYEE_SEARCH", "total_count": 47},
    }
    client = FakeAIClient({
        "provider": "openai",
        "content": "Hiện có 47 nhân viên bạn được phép xem.",
    })

    result = generate_grounded_hr_answer(
        "công ty có bao nhiêu nhân viên",
        response,
        client=client,  # type: ignore[arg-type]
    )

    assert result["reply"] == "Hiện có 47 nhân viên bạn được phép xem."


def test_an_answer_reporting_no_evidence_is_not_given_sources():
    response = {
        "reply": "Không tìm thấy chính sách phù hợp.",
        "citations": [{"id": "chunk-1", "citation_tag": "[Citation: Policy.md, v1.0]"}],
        "tools_executed": [],
        "hr_card": None,
    }
    client = FakeAIClient({
        "provider": "openai",
        "content": "Tôi không tìm thấy thông tin về vấn đề này trong kho tài liệu.",
    })

    result = generate_grounded_hr_answer(
        "chính sách nghỉ thai sản",
        response,
        client=client,  # type: ignore[arg-type]
    )

    assert "Nguồn:" not in result["reply"]
