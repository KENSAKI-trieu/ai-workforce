"""Live routing checks against a real AI service and a real model.

Everything else about these routers is tested with fakes, which prove the wiring and the
guardrails but say nothing about whether a model actually reads Vietnamese business
phrasing the way the prompts assume. These cases are the ones the keyword rules got
wrong, so a passing run here is the evidence that replacing them was worth it.

Skipped unless the service is pointed at explicitly, because it costs money and needs a
network:

    LIVE_AI_SERVICE_URL=http://127.0.0.1:8100 \\
    LIVE_AI_SERVICE_TOKEN=... \\
    .venv/Scripts/python.exe -m pytest tests/test_llm_routing_live.py -v

A label is asserted only where one answer is defensible. Where two are (an ambiguous
paste may fairly be REVIEW or UNSURE), the test accepts the set, because pinning a model
to one of several correct answers only teaches the suite to fail on an upgrade.
"""

from __future__ import annotations

import os
import time

import pytest

from app.core.config import settings
from app.services.agents.hr_llm_flow import classify_hr_request, classify_leave_draft_turn
from app.services.agents.legal_llm_flow import (
    classify_legal_request,
    extract_represented_party,
)
from app.services.ai_service_client import AIServiceClient, get_ai_service_client

LIVE_URL = os.environ.get("LIVE_AI_SERVICE_URL", "").strip()
LIVE_TOKEN = os.environ.get("LIVE_AI_SERVICE_TOKEN", "").strip()
# Free-tier quota is counted per model and per minute, so a run that exhausts one model
# can be repeated on another, and a slow run beats a run whose failures are all 429s.
LIVE_MODEL = os.environ.get("LIVE_LLM_MODEL", "").strip() or None
LIVE_INTERVAL = float(os.environ.get("LIVE_LLM_INTERVAL_SECONDS", "0") or 0)

pytestmark = pytest.mark.skipif(
    not (LIVE_URL and LIVE_TOKEN),
    reason="set LIVE_AI_SERVICE_URL and LIVE_AI_SERVICE_TOKEN to run the live routing checks",
)


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """These call the model directly and never reach the integration database."""
    yield


class PacedClient:
    """The real client, optionally pinned to one model and spaced out between calls.

    A router asks for no particular model, which is right in production and awkward in a
    test: when the configured default runs out of free quota every later assertion fails
    on the echo provider rather than on an answer. Both knobs are off by default.
    """

    def __init__(self, inner: AIServiceClient):
        self.inner = inner
        self.last_call = 0.0

    @property
    def enabled(self) -> bool:
        return self.inner.enabled

    def generate_text(self, messages, **kwargs):
        if LIVE_INTERVAL:
            wait = LIVE_INTERVAL - (time.monotonic() - self.last_call)
            if wait > 0:
                time.sleep(wait)
        try:
            return self.inner.generate_text(messages, model=LIVE_MODEL, **kwargs)
        finally:
            self.last_call = time.monotonic()


@pytest.fixture(scope="module")
def live_client():
    """A client built against the live service, independent of the suite's blank config."""
    original_url, original_token = settings.AI_SERVICE_URL, settings.AI_SERVICE_INTERNAL_TOKEN
    settings.AI_SERVICE_URL = LIVE_URL
    settings.AI_SERVICE_INTERNAL_TOKEN = LIVE_TOKEN
    get_ai_service_client.cache_clear()
    try:
        yield PacedClient(AIServiceClient())
    finally:
        settings.AI_SERVICE_URL = original_url
        settings.AI_SERVICE_INTERNAL_TOKEN = original_token
        get_ai_service_client.cache_clear()


PROSE_CLAUSE = (
    "Bên A chịu trách nhiệm không giới hạn với mọi thiệt hại phát sinh, và Bên B có "
    "quyền đơn phương chấm dứt hợp đồng bất kỳ lúc nào mà không phải bồi thường."
)


@pytest.mark.parametrize(
    "message,accepted",
    [
        # The scorer answered QUESTION for both of these: the first because "?" counted
        # against it, the second because the clauses carry no numbering.
        ("Rà soát giúp tôi hợp đồng NDA này có ổn không?\n\n" + PROSE_CLAUSE, {"REVIEW"}),
        (PROSE_CLAUSE, {"REVIEW", "UNSURE"}),
        # And REVIEW for this one-liner, which is a question about a clause.
        ("Kiểm tra giúp tôi điều khoản phạt 30% có đúng luật không?", {"QUESTION"}),
        ("Indemnification là gì?", {"QUESTION"}),
        ("Quy trình ký hợp đồng với nhà cung cấp mới gồm những bước nào?", {"QUESTION"}),
    ],
)
def test_legal_intent_is_read_from_meaning_not_keywords(live_client, message, accepted):
    result = classify_legal_request(
        message,
        pending_state="NONE",
        fallback_intent="__FALLBACK__",
        client=live_client,
    )

    assert result.source == "llm", "the live service did not answer; check the provider"
    assert result.intent in accepted


_REFUSAL = {"DECLINE_REVIEW", "CANCEL"}


@pytest.mark.parametrize(
    "message,accepted",
    [
        # Refusing and calling off are the same outcome for the caller, so either label
        # is correct here; what matters is that neither reads as a yes.
        ("Đừng rà soát, tôi chỉ hỏi thôi", _REFUSAL),
        ("thôi khỏi, để tôi hỏi phòng pháp chế", _REFUSAL),
        ("ừ rà soát giúp mình đi", {"CONFIRM_REVIEW"}),
        ("đúng rồi bạn", {"CONFIRM_REVIEW"}),
    ],
)
def test_a_pending_offer_reads_yes_apart_from_no(live_client, message, accepted):
    """"đừng" and "đúng" are the same word once tone marks are stripped."""
    result = classify_legal_request(
        message,
        pending_state="AWAITING_INTENT",
        fallback_intent="__FALLBACK__",
        client=live_client,
    )

    assert result.source == "llm"
    assert result.intent in accepted


@pytest.mark.parametrize(
    "message,party",
    [
        ("mình là bên bán", "PARTY_A"),
        ("tôi là bên A, đối tác là bên B", "PARTY_A"),
        ("Bọn mình là bên đi thuê dịch vụ và trả tiền", "PARTY_B"),
        ("công ty tôi cung cấp phần mềm cho họ", "PARTY_A"),
        ("cứ đánh giá khách quan giúp tôi", "NEUTRAL"),
        ("B", "PARTY_B"),
    ],
)
def test_perspective_is_read_from_the_role_described(live_client, message, party):
    result = extract_represented_party(
        message, fallback_party=None, fallback_cancel=False, client=live_client
    )

    assert result.source == "llm"
    assert result.decision == "ANSWER"
    assert result.represented_party == party


@pytest.mark.parametrize(
    "message",
    ["chưa biết nữa", "cái đó quan trọng lắm à?"],
)
def test_a_reply_that_names_no_side_is_not_forced_into_one(live_client, message):
    result = extract_represented_party(
        message, fallback_party=None, fallback_cancel=False, client=live_client
    )

    assert result.source == "llm"
    assert result.represented_party is None


def test_a_conversational_cancellation_is_understood(live_client):
    result = extract_represented_party(
        "thôi bạn ạ, để tôi hỏi phòng pháp chế đã",
        fallback_party=None,
        fallback_cancel=False,
        client=live_client,
    )

    assert result.source == "llm"
    assert result.decision in {"CANCEL", "OTHER"}
    assert result.represented_party is None


COLLECTING_DRAFT = {
    "start_date": None,
    "end_date": None,
    "reason": None,
    "missing_fields": ["start_date", "end_date", "reason"],
}


@pytest.mark.parametrize(
    "message,expected",
    [
        # Keywords read this as the answer to the slot being collected, so it would have
        # become the reason for leave.
        ("Sếp vừa bảo không cần rồi, bỏ đơn giúp mình nhé", "CANCEL"),
        ("Mình đổi ý rồi, dẹp cái đơn đi", "CANCEL"),
        # A date word inside a policy question is not an answer either.
        ("Mà tuần sau công ty có quy định gì mới về nghỉ phép không?", "UNRELATED"),
        ("từ 2026-10-05 đến 2026-10-07, lý do đi khám bệnh", "CONTINUE"),
        ("cho mình nghỉ ngày mai nhé, nhà có việc", "CONTINUE"),
    ],
)
def test_leave_draft_turn_is_read_from_the_sentence(live_client, message, expected):
    result = classify_leave_draft_turn(
        message,
        draft=COLLECTING_DRAFT,
        fallback_turn="__FALLBACK__",
        client=live_client,
    )

    assert result.source == "llm"
    assert result.turn == expected


@pytest.mark.parametrize(
    "message,kind,intent",
    [
        ("Tôi còn bao nhiêu ngày phép?", "QUESTION", "QUERY_LEAVE_BALANCE"),
        ("Cho tôi xin danh sách nhân viên phòng Sales", "QUESTION", "EMPLOYEE_DIRECTORY"),
        ("Tạo đơn nghỉ phép cho tôi ngày mai", "ACTION", "ACTION_LEAVE_REQUEST"),
        ("Làm sao để nộp đơn nghỉ phép?", "QUESTION", "POLICY_QUERY"),
    ],
)
def test_hr_router_still_reads_the_core_intents(live_client, message, kind, intent):
    """The HR router is unchanged; this is the regression net around its prompt."""
    result = classify_hr_request(message, detailed_intent="UNKNOWN", client=live_client)

    assert result.source == "llm"
    assert result.kind == kind
    assert result.intent == intent
