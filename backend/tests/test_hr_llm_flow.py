from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.agents import agent_executor
from app.services.agents.hr_llm_flow import (
    HRRequestClassification,
    classify_hr_request,
    generate_grounded_hr_answer,
)


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """These unit tests use fakes only and do not require the integration database."""
    yield


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
        "tools_executed": [{"tool_name": "hybrid_rag_search", "result_count": 1}],
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
    monkeypatch.setattr(
        agent_executor,
        "_classify_hr_intent",
        lambda _message: "ACTION_LEAVE_REQUEST",
    )
    monkeypatch.setattr(agent_executor, "_load_leave_draft", lambda *_args: None)
    monkeypatch.setattr(
        agent_executor,
        "classify_hr_request",
        lambda *_args, **_kwargs: HRRequestClassification("QUESTION", "llm"),
    )

    def fake_core(*_args, **kwargs):
        captured["intent"] = kwargs["hr_intent_override"]
        return {"reply": "retrieved", "citations": [], "tools_executed": [], "hr_card": None}

    monkeypatch.setattr(agent_executor, "_execute_agent_chat_core", fake_core)
    monkeypatch.setattr(
        agent_executor,
        "generate_grounded_hr_answer",
        lambda _message, response: {**response, "generated": True},
    )

    result = agent_executor.execute_agent_chat(
        SimpleNamespace(),
        SimpleNamespace(),
        "HR",
        "Tôi có thể xin nghỉ ngày mai không?",
    )

    assert captured["intent"] == "POLICY_QUERY"
    assert result["generated"] is True
