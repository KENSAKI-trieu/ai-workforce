"""Web search: what is kept from a grounded Gemini answer, and what is dropped."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import web_search
from app.services.web_search import WebSearchUnavailable, search_web

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def _chunk(title: str, uri: str) -> SimpleNamespace:
    return SimpleNamespace(web=SimpleNamespace(title=title, uri=uri))


def _support(text: str, *indices: int) -> SimpleNamespace:
    return SimpleNamespace(segment=SimpleNamespace(text=text), grounding_chunk_indices=list(indices))


def _response(text: str, chunks: list, supports: list, queries: list[str]) -> SimpleNamespace:
    metadata = SimpleNamespace(grounding_chunks=chunks, grounding_supports=supports, web_search_queries=queries)
    usage = SimpleNamespace(
        prompt_token_count=60,
        tool_use_prompt_token_count=180,
        candidates_token_count=400,
        thoughts_token_count=300,
        cached_content_token_count=0,
    )
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(grounding_metadata=metadata)], usage_metadata=usage)


@pytest.fixture
def resolved(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Redirect links resolve to whatever the test maps them to."""
    table: dict[str, str] = {}
    monkeypatch.setattr(web_search, "_resolve", lambda _client, url: table.get(url, url))
    return table


def test_a_grounded_answer_keeps_each_page_with_the_sentences_it_supports(monkeypatch, resolved) -> None:
    resolved[REDIRECT + "a"] = "https://www.baomoi.com/gia-ca-phe.epi"
    resolved[REDIRECT + "b"] = "https://thitruong.vn/robusta"
    monkeypatch.setattr(web_search, "_generate", lambda _query: _response(
        "- Giá robusta tăng.",
        [_chunk("baomoi.com", REDIRECT + "a"), _chunk("thitruong.vn", REDIRECT + "b")],
        [_support("**Giá robusta** tăng 2%.", 0), _support("* Tồn kho giảm.", 0, 1)],
        ["giá robusta hôm nay"],
    ))

    result = search_web("giá cà phê", max_results=5)

    assert result["grounded"] is True
    assert result["summary"] == "- Giá robusta tăng."
    assert result["queries"] == ["giá robusta hôm nay"]
    assert [item["url"] for item in result["results"]] == [
        "https://www.baomoi.com/gia-ca-phe.epi",
        "https://thitruong.vn/robusta",
    ]
    # Plain text: the summary's bold marks and bullets are not part of a snippet.
    assert result["results"][0]["snippet"] == "Giá robusta tăng 2%. Tồn kho giảm."
    assert result["results"][0]["site"] == "baomoi.com"
    # The search tool's prompt is billed as input, thinking as output.
    assert result["usage"]["prompt_tokens"] == 240
    assert result["usage"]["completion_tokens"] == 700


def test_an_answer_written_without_searching_is_not_passed_off_as_a_web_result(monkeypatch, resolved) -> None:
    monkeypatch.setattr(web_search, "_generate", lambda _query: _response("Theo hiểu biết của tôi…", [], [], []))

    result = search_web("xu hướng 2026")

    assert result["grounded"] is False
    assert result["summary"] == ""
    assert result["results"] == []


def test_pages_backing_a_sentence_come_first_and_duplicates_are_merged(monkeypatch, resolved) -> None:
    resolved[REDIRECT + "x"] = "https://same.vn/page"
    resolved[REDIRECT + "y"] = "https://same.vn/page"
    monkeypatch.setattr(web_search, "_generate", lambda _query: _response(
        "- Ý.",
        [_chunk("unused.vn", "https://unused.vn"), _chunk("same.vn", REDIRECT + "x"), _chunk("same.vn", REDIRECT + "y")],
        [_support("Câu có nguồn.", 1, 2)],
        [],
    ))

    result = search_web("q", max_results=5)

    assert [item["url"] for item in result["results"]] == ["https://same.vn/page", "https://unused.vn"]
    assert result["results"][1]["snippet"] == ""


def test_no_key_means_no_search(monkeypatch) -> None:
    monkeypatch.setattr(web_search.settings, "GOOGLE_AI_API_KEY", None)
    monkeypatch.setattr(web_search.settings, "GOOGLE_AI_API_KEY_2", None)

    with pytest.raises(WebSearchUnavailable):
        search_web("q")


def test_the_route_answers_503_when_search_is_unavailable(monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise WebSearchUnavailable("quota")

    monkeypatch.setattr("app.api.routes.web.search_web", refuse)
    response = TestClient(app).post("/v1/web/search", json={"query": "xu hướng"})

    assert response.status_code == 503
