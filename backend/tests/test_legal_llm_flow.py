"""The Legal router asks a model, but never lets one widen what the agent may do.

These tests use a fake client, so they pin the contract around the model rather than the
model itself: a label outside the closed list, a reply that is not JSON, the echo
provider and an unreachable service must all leave the caller with the deterministic
answer it already had. The live behaviour is covered by test_llm_routing_live.py.
"""

from __future__ import annotations

import json

import pytest

from app.services.agents.legal_llm_flow import (
    CLASSIFIER_MESSAGE_LIMIT,
    LegalIntentClassification,
    LegalPerspective,
    classify_legal_request,
    extract_represented_party,
)


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Fakes only: these tests never reach the integration database."""
    yield


class FakeAIClient:
    enabled = True

    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def generate_text(self, messages, **_kwargs):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("the router made more calls than the test expected")
        return self.responses.pop(0)


class UnreachableAIClient:
    enabled = True

    def generate_text(self, _messages, **_kwargs):
        from app.services.ai_service_client import AIServiceError

        raise AIServiceError("gateway down", status_code=503)


class DisabledAIClient:
    enabled = False

    def generate_text(self, _messages, **_kwargs):  # pragma: no cover - never called
        raise AssertionError("a disabled client must not be called")


def _llm(payload: dict) -> dict:
    return {"provider": "gemini", "content": json.dumps(payload, ensure_ascii=False)}


def test_router_label_wins_over_the_keyword_scorer():
    client = FakeAIClient(_llm({"intent": "REVIEW"}))

    result = classify_legal_request(
        "Rà soát hợp đồng này giúp tôi, mức phạt 30% có ổn không?",
        pending_state="NONE",
        fallback_intent="QUESTION",
        client=client,  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("REVIEW", "llm")


def test_classifier_sees_the_pending_state_and_a_bounded_message():
    long_contract = "Điều 1. " + ("nội dung " * 2000) + "Điều khoản trên có ổn không?"
    client = FakeAIClient(_llm({"intent": "REVIEW"}))

    classify_legal_request(
        long_contract,
        pending_state="AWAITING_INTENT",
        fallback_intent="UNSURE",
        client=client,  # type: ignore[arg-type]
    )

    payload = json.loads(client.calls[0][-1]["content"])
    assert payload["pending_state"] == "AWAITING_INTENT"
    # Trimmed rather than sent whole: the opening identifies a contract, and the rest
    # would bill the tenant for tokens that cannot change the label -- except the close,
    # which is where a request written after the paste sits.
    assert len(payload["message"]) <= CLASSIFIER_MESSAGE_LIMIT
    assert payload["message"].startswith("Điều 1. nội dung")
    assert payload["message"].endswith("Điều khoản trên có ổn không?")


@pytest.mark.parametrize("label", ["CONFIRM_REVIEW", "DECLINE_REVIEW", "CANCEL"])
def test_pending_only_labels_are_refused_when_nothing_is_pending(label):
    """Confirming a question nobody asked must not start or stop a review.

    The misread is not believed, so the scorer's answer stands -- not a forced
    QUESTION, which would send a contract the scorer recognised into retrieval.
    """
    client = FakeAIClient(_llm({"intent": label}))

    result = classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=client,  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("REVIEW", "fallback")


def test_cancel_from_an_overridden_prompt_is_read_as_declining():
    """Callers handle one refusal label, whatever vocabulary a tenant prompt uses."""
    client = FakeAIClient(_llm({"intent": "CANCEL"}))

    result = classify_legal_request(
        "thôi khỏi",
        pending_state="AWAITING_INTENT",
        fallback_intent="DECLINE_REVIEW",
        client=client,  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("DECLINE_REVIEW", "llm")


def test_an_invented_label_keeps_the_deterministic_answer():
    client = FakeAIClient(_llm({"intent": "AUDIT_AND_SIGN"}))

    result = classify_legal_request(
        "Rà soát hợp đồng",
        pending_state="NONE",
        fallback_intent="UNSURE",
        client=client,  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("UNSURE", "fallback")


def test_unparsable_reply_keeps_the_deterministic_answer():
    client = FakeAIClient({"provider": "gemini", "content": "Chắc là hợp đồng đấy bạn"})

    result = classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=client,  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("REVIEW", "fallback")


def test_echo_provider_is_not_treated_as_a_classifier():
    client = FakeAIClient({"provider": "local", "content": "Local provider received: ..."})

    result = classify_legal_request(
        "Indemnification là gì?",
        pending_state="NONE",
        fallback_intent="QUESTION",
        client=client,  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("QUESTION", "fallback")


def test_a_reply_body_that_is_not_an_object_keeps_the_deterministic_answer():
    """A list or string from the service must not surface as a 500."""
    intent = classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=FakeAIClient(["REVIEW"]),  # type: ignore[arg-type]
    )
    perspective = extract_represented_party(
        "bên A",
        fallback_party="PARTY_A",
        fallback_cancel=False,
        client=FakeAIClient("PARTY_A"),  # type: ignore[arg-type]
    )

    assert intent == LegalIntentClassification("REVIEW", "fallback")
    assert perspective == LegalPerspective("PARTY_A", "ANSWER", "fallback")


def test_a_failing_ai_service_never_breaks_the_turn():
    result = classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=UnreachableAIClient(),  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("REVIEW", "fallback")


def test_a_disabled_ai_service_uses_the_scorer_without_calling_out():
    result = classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=DisabledAIClient(),  # type: ignore[arg-type]
    )

    assert result == LegalIntentClassification("REVIEW", "fallback")


def test_usage_is_metered_for_a_billed_call_but_not_for_the_echo_provider():
    metered: list[dict] = []
    classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=FakeAIClient(_llm({"intent": "REVIEW"})),  # type: ignore[arg-type]
        on_usage=metered.append,
    )
    classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=FakeAIClient({"provider": "local", "content": "echo"}),  # type: ignore[arg-type]
        on_usage=metered.append,
    )

    assert len(metered) == 1


def test_perspective_reads_the_side_the_speaker_claims():
    client = FakeAIClient(_llm({"represented_party": "PARTY_A", "decision": "ANSWER"}))

    result = extract_represented_party(
        "tôi là bên A, đối tác là bên B",
        fallback_party=None,
        fallback_cancel=False,
        client=client,  # type: ignore[arg-type]
    )

    assert result == LegalPerspective("PARTY_A", "ANSWER", "llm")


def test_perspective_reports_a_cancellation():
    client = FakeAIClient(_llm({"represented_party": None, "decision": "CANCEL"}))

    result = extract_represented_party(
        "thôi bỏ đi, tôi không cần rà soát nữa",
        fallback_party=None,
        fallback_cancel=False,
        client=client,  # type: ignore[arg-type]
    )

    assert result.decision == "CANCEL"
    assert result.represented_party is None


def test_an_answer_that_names_no_side_is_not_an_answer():
    """ANSWER with a missing party would otherwise audit with a null perspective."""
    client = FakeAIClient(_llm({"represented_party": None, "decision": "ANSWER"}))

    result = extract_represented_party(
        "chưa biết nữa",
        fallback_party=None,
        fallback_cancel=False,
        client=client,  # type: ignore[arg-type]
    )

    assert result.decision == "OTHER"
    assert result.represented_party is None


def test_an_invented_party_keeps_the_keyword_reading():
    client = FakeAIClient(_llm({"represented_party": "PARTY_C", "decision": "ANSWER"}))

    result = extract_represented_party(
        "bên C nhé",
        fallback_party=None,
        fallback_cancel=False,
        client=client,  # type: ignore[arg-type]
    )

    assert result.represented_party is None


def test_perspective_falls_back_to_the_keyword_reading_when_the_service_fails():
    result = extract_represented_party(
        "mình là bên bán",
        fallback_party="PARTY_A",
        fallback_cancel=False,
        client=UnreachableAIClient(),  # type: ignore[arg-type]
    )

    assert result == LegalPerspective("PARTY_A", "ANSWER", "fallback")


def test_both_legal_routers_use_the_short_router_timeout(monkeypatch):
    """A stalled provider must cost the user seconds, not the generation timeout."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_SERVICE_ROUTER_TIMEOUT_SECONDS", 9.0)
    timeouts: list[float | None] = []

    class TimedClient(FakeAIClient):
        def generate_text(self, messages, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            return super().generate_text(messages)

    classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=TimedClient(_llm({"intent": "REVIEW"})),  # type: ignore[arg-type]
    )
    extract_represented_party(
        "bên A",
        fallback_party="PARTY_A",
        fallback_cancel=False,
        client=TimedClient(  # type: ignore[arg-type]
            _llm({"represented_party": "PARTY_A", "decision": "ANSWER"})
        ),
    )

    assert timeouts == [9.0, 9.0]


@pytest.mark.parametrize(
    "reply,scope",
    [
        ({"intent": "REVIEW", "scope": "excerpt"}, "EXCERPT"),
        ({"intent": "UNSURE", "scope": "FULL"}, "FULL"),
        # A scope is only meaningful for a turn that carries a document.
        ({"intent": "QUESTION", "scope": "FULL"}, None),
        ({"intent": "REVIEW", "scope": "HALF"}, None),
        ({"intent": "REVIEW"}, None),
    ],
)
def test_the_router_reports_whether_the_text_is_whole_or_a_piece(reply, scope):
    result = classify_legal_request(
        "Điều 1. Phạt 30%",
        pending_state="NONE",
        fallback_intent="REVIEW",
        client=FakeAIClient(_llm(reply)),  # type: ignore[arg-type]
    )

    assert result.document_scope == scope
