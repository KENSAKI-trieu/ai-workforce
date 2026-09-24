"""Legal questions are answered from retrieved documents, and only when they answer it.

Retrieval returns its nearest excerpts whether or not any is about the question. Shown
as they were, a question about warranty periods was answered with the leave policy under
"Theo văn bản pháp luật...". The model now decides whether the excerpts answer the
question; the fake clients here pin what the code does with each of its answers.
"""

from __future__ import annotations

import json

import pytest

from app.services.agents import legal_llm_flow
from app.services.agents.legal_llm_flow import (
    EVIDENCE_CHARS_PER_EXCERPT,
    LegalGroundedAnswer,
    answer_from_legal_evidence,
)

EVIDENCE = [
    {
        "document_title": "Ghi chú pháp chế",
        "section_title": "Mức phạt vi phạm",
        "content": "Mức phạt không vượt quá 8% giá trị phần nghĩa vụ bị vi phạm.",
        "citation_tag": "[Citation: Ghi chú pháp chế; chunk=1]",
    },
    {
        "document_title": "Chính sách nghỉ phép",
        "section_title": "Quyền lợi",
        "content": "Mỗi nhân viên có 12 ngày nghỉ phép năm.",
        "citation_tag": "[Citation: Chính sách nghỉ phép; chunk=2]",
    },
]

LEAVE_QUESTION = "Nhân viên được nghỉ phép bao nhiêu ngày một năm?"


class Router:
    """Plays both Legal models: the intent router and the grounded answer."""

    enabled = True

    def __init__(self, answer: dict | str, intent: str = "QUESTION"):
        self.answer = answer
        self.intent = intent
        self.answer_calls: list[dict] = []

    def generate_text(self, messages, **_kwargs):
        payload = json.loads(messages[-1]["content"])
        if "evidence" in payload:
            self.answer_calls.append(payload)
            content = self.answer if isinstance(self.answer, str) else json.dumps(
                self.answer, ensure_ascii=False
            )
        else:
            content = json.dumps({"intent": self.intent})
        return {"provider": "gemini", "content": content}


# --- The function ---------------------------------------------------------------


def _answer(reply, evidence=EVIDENCE):
    return answer_from_legal_evidence(
        "Mức phạt tối đa là bao nhiêu?",
        evidence,
        client=Router(reply),  # type: ignore[arg-type]
    )


def test_an_answer_keeps_only_the_excerpts_it_cites():
    result = _answer({"answerable": True, "answer": "Tối đa 8%.", "sources": ["s1"]})

    assert result == LegalGroundedAnswer(True, "Tối đa 8%.", (0,))


def test_excerpts_that_do_not_answer_are_reported_as_such():
    assert _answer({"answerable": False}) == LegalGroundedAnswer(False, "", ())


@pytest.mark.parametrize(
    "reply",
    [
        # Claims an answer but grounds it in nothing it was given.
        {"answerable": True, "answer": "Tối đa 8%.", "sources": []},
        {"answerable": True, "answer": "Tối đa 8%.", "sources": ["S9"]},
        {"answerable": True, "answer": "", "sources": ["S1"]},
        # Not the contract at all.
        {"answer": "Tối đa 8%."},
        "Theo tôi là 8%",
    ],
)
def test_an_answer_that_cannot_be_believed_is_not_used(reply):
    assert _answer(reply) is None


def test_no_evidence_means_no_model_call():
    router = Router({"answerable": True, "answer": "x", "sources": ["S1"]})

    assert answer_from_legal_evidence("q", [], client=router) is None  # type: ignore[arg-type]
    assert router.answer_calls == []


def test_the_model_sees_numbered_bounded_excerpts():
    router = Router({"answerable": False})
    long_excerpt = [{**EVIDENCE[0], "content": "x" * (EVIDENCE_CHARS_PER_EXCERPT + 500)}]

    answer_from_legal_evidence("q", long_excerpt + EVIDENCE[1:], client=router)  # type: ignore[arg-type]

    sent = router.answer_calls[0]["evidence"]
    assert [item["ref"] for item in sent] == ["S1", "S2"]
    assert len(sent[0]["content"]) == EVIDENCE_CHARS_PER_EXCERPT
    # Citation tags carry chunk ids the answer has no use for.
    assert "citation_tag" not in sent[0]


def test_the_echo_provider_is_not_an_answer():
    class Echo:
        enabled = True

        def generate_text(self, _messages, **_kwargs):
            return {"provider": "local", "content": '{"answerable": false}'}

    assert answer_from_legal_evidence("q", EVIDENCE, client=Echo()) is None  # type: ignore[arg-type]


# --- Through the chat -----------------------------------------------------------


def _chat(client, headers, message):
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "LEGAL", "message": message},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _search_finds_something(client, headers):
    result = _chat(client, headers, LEAVE_QUESTION)
    search = next(t for t in result["tools_executed"] if t["tool_name"] == "rag_search")
    assert search["result_count"] > 0, "the seeded knowledge base should match this query"


def test_an_answered_question_cites_only_what_the_answer_used(
    client, employee_token_headers, monkeypatch
):
    _search_finds_something(client, employee_token_headers)
    router = Router({"answerable": True, "answer": "Mười hai ngày.", "sources": ["S1"]})
    monkeypatch.setattr(legal_llm_flow, "get_ai_service_client", lambda: router)

    result = _chat(client, employee_token_headers, LEAVE_QUESTION)

    assert result["reply"].startswith("Mười hai ngày.")
    assert len(result["citations"]) == 1
    assert result["citations"][0]["citation_tag"] in result["reply"]
    assert "chưa rà soát rủi ro nội dung này" in result["reply"]


def test_excerpts_that_do_not_answer_are_not_shown_as_the_law(
    client, employee_token_headers, monkeypatch
):
    _search_finds_something(client, employee_token_headers)
    monkeypatch.setattr(
        legal_llm_flow, "get_ai_service_client", lambda: Router({"answerable": False})
    )

    result = _chat(client, employee_token_headers, LEAVE_QUESTION)

    assert result["citations"] == []
    assert "Theo văn bản" not in result["reply"]
    assert "chưa tìm thấy văn bản" in result["reply"]


def test_without_a_model_the_nearest_excerpts_are_shown_but_not_asserted(
    client, employee_token_headers
):
    result = _chat(client, employee_token_headers, LEAVE_QUESTION)

    assert result["citations"]
    # Shown as the nearest text found, never asserted to be the answer.
    assert result["reply"].startswith("Tôi chưa thể tổng hợp câu trả lời")
    assert "Theo văn bản" not in result["reply"]


def test_a_term_is_no_longer_defined_from_a_hardcoded_glossary(
    client, employee_token_headers
):
    result = _chat(client, employee_token_headers, "Force majeure là gì?")

    assert "legal_glossary_lookup" not in [t["tool_name"] for t in result["tools_executed"]]
    assert "sự kiện ngoài khả năng kiểm soát" not in result["reply"]
