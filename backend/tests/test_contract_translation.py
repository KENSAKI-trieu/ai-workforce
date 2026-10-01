"""A contract not in Vietnamese is reviewed from a translation, or not scored at all.

The analyzer is Vietnamese rules with a few English phrases. "The Receiving Party shall be
liable for unlimited damages" scored 0/100 LOW, where the same clause in Vietnamese scored
HIGH. The fake translator here stands in for the model; what is pinned is what the code does
with its reply, and that nothing is scored when there is no translation to score.
"""

from __future__ import annotations

import uuid

import pytest

from app.domains.legal import contract_translation
from app.domains.legal.contract_translation import (
    MAX_TRANSLATED_CHARS,
    TOO_LONG_REPLY,
    TRANSLATION_NOTICE,
    UNAVAILABLE_REPLY,
    ContractNotReviewable,
    text_for_review,
)
from app.models.models import ContractReview
from tests.test_contract_review_gateway_tool import _conversation, _invoke, employee  # noqa: F401

ENGLISH_CLAUSE = "The Receiving Party shall be liable for unlimited damages for any breach."
VIETNAMESE_CLAUSE = "Bên Nhận chịu trách nhiệm bồi thường không giới hạn đối với mọi vi phạm."


class Translator:
    """Plays the model: reports the language, and translates known text."""

    enabled = True

    def __init__(self, reply: str | None = None, *, provider: str = "gemini") -> None:
        self.reply = reply
        self.provider = provider
        self.calls: list[str] = []

    def generate_text(self, messages, **_kwargs):
        text = messages[-1]["content"]
        self.calls.append(text)
        if self.reply is not None:
            content = self.reply
        elif ENGLISH_CLAUSE in text:
            content = f"LANGUAGE: en\n{VIETNAMESE_CLAUSE}"
        else:
            content = "LANGUAGE: vi"
        return {"provider": self.provider, "model": "fake", "content": content}


class Disabled:
    enabled = False


# --- The function ---------------------------------------------------------------


def test_plainly_vietnamese_text_never_reaches_the_model() -> None:
    translator = Translator()
    result = text_for_review(VIETNAMESE_CLAUSE, client=translator)  # type: ignore[arg-type]
    assert (result.text, result.translated) == (VIETNAMESE_CLAUSE, False)
    assert translator.calls == []


def test_english_is_reviewed_from_its_translation_and_metered() -> None:
    usage: list[dict] = []
    result = text_for_review(
        ENGLISH_CLAUSE, client=Translator(), on_usage=usage.append  # type: ignore[arg-type]
    )
    assert result.text == VIETNAMESE_CLAUSE
    assert (result.source_language, result.translated) == ("en", True)
    assert len(usage) == 1


def test_unaccented_vietnamese_is_the_models_call_and_is_not_rewritten() -> None:
    text = "Ben A chiu trach nhiem boi thuong khong gioi han."
    translator = Translator()
    result = text_for_review(text, client=translator)  # type: ignore[arg-type]
    assert len(translator.calls) == 1
    assert (result.text, result.translated) == (text, False)


@pytest.mark.parametrize("client", [
    Disabled(),
    Translator(provider="local"),  # the echo provider translates nothing
    Translator("Here is the translation: ..."),  # not the agreed format
    Translator("LANGUAGE: en"),  # says it is English, translates nothing
])
def test_without_a_translation_nothing_is_scored(client) -> None:
    with pytest.raises(ContractNotReviewable) as raised:
        text_for_review(ENGLISH_CLAUSE, client=client)  # type: ignore[arg-type]
    assert raised.value.reply == UNAVAILABLE_REPLY


def test_a_contract_longer_than_one_turn_can_translate_is_refused_before_any_call() -> None:
    translator = Translator()
    long_text = "\n\n".join([ENGLISH_CLAUSE] * (MAX_TRANSLATED_CHARS // len(ENGLISH_CLAUSE) + 50))
    with pytest.raises(ContractNotReviewable) as raised:
        text_for_review(long_text, client=translator)  # type: ignore[arg-type]
    assert raised.value.reply == TOO_LONG_REPLY
    assert translator.calls == []


def test_a_long_contract_is_translated_in_order_block_by_block() -> None:
    paragraphs = [f"Clause {index}. {ENGLISH_CLAUSE}" for index in range(300)]
    text = "\n\n".join(paragraphs)
    translator = Translator()

    def generate_text(messages, **_kwargs):
        block = messages[-1]["content"]
        translator.calls.append(block)
        return {"provider": "gemini", "content": "LANGUAGE: en\n" + block.replace("Clause", "Điều")}

    translator.generate_text = generate_text  # type: ignore[method-assign]
    result = text_for_review(text, client=translator)  # type: ignore[arg-type]
    assert len(translator.calls) > 1
    assert result.text.split("\n\n") == [p.replace("Clause", "Điều") for p in paragraphs]


# --- Through the gateway tool, which LangGraph reviews with ----------------------


def test_an_english_clause_is_scored_from_its_translation(
    client, transactional_db_session, employee, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(contract_translation, "get_ai_service_client", lambda: Translator())
    conversation = _conversation(transactional_db_session, employee, ENGLISH_CLAUSE, "bên B")

    response = _invoke(
        client, employee, conversation.id, from_user_message=1, document_scope="EXCERPT"
    )

    result = response.json()["result"]
    assert result["status"] == "REVIEWED"
    # 0/100 LOW before: the English wording matched none of the rules.
    assert result["risk_score"] > 0
    assert any(finding["category"] == "LIABILITY" for finding in result["findings"])
    assert TRANSLATION_NOTICE in result["reply"]
    review = transactional_db_session.get(ContractReview, uuid.UUID(result["review_id"]))
    # Kept under what the user sent; the findings quote the translation.
    assert review.contract_text == ENGLISH_CLAUSE
    assert review.result["translated_for_review"] is True
    assert review.result["source_language"] == "en"


def test_an_english_clause_that_cannot_be_translated_is_not_scored(
    client, transactional_db_session, employee, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(contract_translation, "get_ai_service_client", lambda: Disabled())
    conversation = _conversation(transactional_db_session, employee, ENGLISH_CLAUSE, "bên B")
    before = transactional_db_session.query(ContractReview).count()

    response = _invoke(client, employee, conversation.id, from_user_message=1)

    result = response.json()["result"]
    assert result == {"status": "NOT_REVIEWABLE", "reviewed": False, "reply": UNAVAILABLE_REPLY}
    assert transactional_db_session.query(ContractReview).count() == before


def test_an_uploaded_english_contract_is_refused_rather_than_scored_wrong(
    client, employee_token_headers, monkeypatch
):
    monkeypatch.setattr(contract_translation, "get_ai_service_client", lambda: Disabled())
    response = client.post(
        "/api/v1/legal/review-document",
        files={"file": ("nda.txt", ENGLISH_CLAUSE.encode(), "text/plain")},
        data={"represented_party": "PARTY_B"},
        headers=employee_token_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == UNAVAILABLE_REPLY


def test_the_translator_never_sees_personal_data_and_the_translation_gets_it_back():
    text = "EMPLOYMENT CONTRACT\nEmployee: John Smith, phone 0912 345 678.\nClause 1. Mr. John Smith works for twelve months."
    translator = Translator()

    def generate_text(messages, **_kwargs):
        block = messages[-1]["content"]
        translator.calls.append(block)
        assert "Stand-ins" in messages[0]["content"] or "stand-ins" in messages[0]["content"]
        return {"provider": "gemini", "content": "LANGUAGE: en\n" + block.replace("Clause", "Điều").replace("works for twelve months", "làm việc 12 tháng")}

    translator.generate_text = generate_text  # type: ignore[method-assign]
    result = text_for_review(text, client=translator)  # type: ignore[arg-type]

    assert "John Smith" not in translator.calls[0] and "0912 345 678" not in translator.calls[0]
    assert "[NGƯỜI_1]" in translator.calls[0]
    assert result.translated and "Mr. John Smith làm việc 12 tháng" in result.text
    assert "0912 345 678" in result.text
