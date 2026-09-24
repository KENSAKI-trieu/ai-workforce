from datetime import date
from types import SimpleNamespace

import pytest

from tests.chat_patching import patch_chat
from app.agents.hr.intent import _classify_hr_intent
from app.agents.hr.leave import (
    _extract_leave_slots,
    _extract_leave_slots_with_llm,
    _is_leave_draft_continuation,
    _leave_date_context,
    _parse_leave_date,
)


def test_parse_leave_date_defaults_missing_year_to_current_year():
    current_year = date.today().year

    assert _parse_leave_date("7/8") == date(current_year, 8, 7)
    assert _parse_leave_date("07-08") == date(current_year, 8, 7)


def test_extract_leave_slots_accepts_date_range_without_year():
    current_year = date.today().year

    slots = _extract_leave_slots(
        "ngày bắt đầu 7/8 kết thúc 9/8 lý do là về quê"
    )

    assert slots == {
        "start_date": date(current_year, 8, 7).isoformat(),
        "end_date": date(current_year, 8, 9).isoformat(),
        "reason": "về quê",
    }


def test_parse_leave_date_keeps_explicit_year_and_rejects_invalid_dates():
    assert _parse_leave_date("7/8/2030") == date(2030, 8, 7)
    assert _parse_leave_date("2030-08-07") == date(2030, 8, 7)
    assert _parse_leave_date("31/2") is None


def test_llm_leave_slots_override_parser_and_keep_existing_draft(monkeypatch):
    patch_chat(monkeypatch, "extract_leave_request_slots",
        lambda *_args, **_kwargs: {
            "start_date": None,
            "end_date": "2026-08-29",
            "reason": "về quê",
        },
    )

    slots = _extract_leave_slots_with_llm(
        "đến ngày kia vì về quê",
        {"start_date": "2026-08-28", "end_date": None, "reason": None},
        reference_date=date(2026, 8, 27),
        timezone_name="Asia/Ho_Chi_Minh",
    )

    assert slots == {
        "start_date": "2026-08-28",
        "end_date": "2026-08-29",
        "reason": "về quê",
    }


def test_a_bare_date_corrects_the_end_date_after_a_validation_error():
    """Both slots are filled after a rejected pair, so the correction needs its own branch."""
    rejected = {
        "start_date": "2030-09-25",
        "end_date": "2030-09-20",
        "reason": "việc gia đình",
        "missing_fields": [],
        "validation_error": "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu.",
    }

    slots = _extract_leave_slots("28/09/2030", rejected)

    assert slots["start_date"] == "2030-09-25"
    assert slots["end_date"] == "2030-09-28"
    assert slots["reason"] == "việc gia đình"


def test_a_bare_date_on_a_healthy_draft_does_not_overwrite_the_end_date():
    """Without a rejection there is nothing to correct, so filled slots stay put."""
    healthy = {
        "start_date": "2030-09-20",
        "end_date": "2030-09-25",
        "reason": "việc gia đình",
        "missing_fields": [],
        "validation_error": None,
    }

    slots = _extract_leave_slots("28/09/2030", healthy)

    assert slots["start_date"] == "2030-09-20"
    assert slots["end_date"] == "2030-09-25"


def test_correcting_the_start_date_still_honours_its_marker():
    """The bare-date fallback targets the end date; an explicit marker overrides it."""
    rejected = {
        "start_date": "2030-09-25",
        "end_date": "2030-09-20",
        "reason": "việc gia đình",
        "missing_fields": [],
        "validation_error": "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu.",
    }

    slots = _extract_leave_slots("bắt đầu 18/09/2030", rejected)

    assert slots["start_date"] == "2030-09-18"
    assert slots["end_date"] == "2030-09-20"


def test_relative_date_is_recognized_as_leave_draft_continuation():
    draft = {"missing_fields": ["end_date", "reason"]}

    assert _is_leave_draft_continuation("Đến ngày kia", draft) is True
    assert _is_leave_draft_continuation("Thứ sáu tuần sau", draft) is True


def test_leave_date_context_queries_timezone_without_user_relationship():
    class FakeQuery:
        def filter(self, *_args):
            return self

        def scalar(self):
            return "Asia/Ho_Chi_Minh"

    class FakeSession:
        def query(self, *_args):
            return FakeQuery()

    local_date, timezone_name = _leave_date_context(
        FakeSession(),  # type: ignore[arg-type]
        SimpleNamespace(tenant_id="tenant-id"),  # type: ignore[arg-type]
    )

    assert local_date == date.today()
    assert timezone_name == "Asia/Ho_Chi_Minh"


def test_a_policy_question_mentioning_a_date_is_not_a_draft_continuation():
    """A date is not consent to submit: mid-draft questions keep their own route."""
    draft = {"missing_fields": ["reason"]}

    assert _is_leave_draft_continuation("Nghỉ ngày 20/12 có bị trừ lương không?", draft) is False
    assert _is_leave_draft_continuation("Quy định nghỉ ngày 20/12 thế nào", draft) is False
    assert _is_leave_draft_continuation("Nghỉ ốm được bao nhiêu ngày", draft) is False
    # A plain answer while the reason is still missing is still that reason.
    assert _is_leave_draft_continuation("Về quê ăn cưới", draft) is True
    assert _is_leave_draft_continuation("Từ ngày 20/12", {"missing_fields": ["start_date"]}) is True


@pytest.mark.parametrize(
    "message",
    [
        # Phrasings the intent classifier treats as policy questions must never be
        # recorded as the reason on an open draft.
        "Nghỉ phép có cần đơn không",
        "Nghỉ không lương có được không",
        "Điều kiện nghỉ phép năm",
        "Hướng dẫn xin nghỉ",
        "Nghỉ phép được mấy ngày",
        "Thủ tục ra sao",
    ],
)
def test_policy_phrasings_never_become_a_leave_reason(message):
    assert _is_leave_draft_continuation(message, {"missing_fields": ["reason"]}) is False
    assert _classify_hr_intent(message) == "POLICY_QUERY"
