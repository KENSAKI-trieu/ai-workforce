import math

import pytest

from app.rag.reranking.jina import JinaAPIReranker


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


def test_jina_scores_are_restored_to_input_order(monkeypatch) -> None:
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse({
            "results": [
                {"index": 1, "relevance_score": 0.91},
                {"index": 0, "relevance_score": 0.25},
            ]
        })

    monkeypatch.setattr("app.rag.reranking.jina.httpx.post", fake_post)
    provider = JinaAPIReranker(
        api_key="test-key",
        model_name="jina-reranker-v3.5",
        timeout=12,
    )

    scores = provider.score("leave policy", ["first", "second"], [{}, {}])

    assert scores == pytest.approx([
        1.0 / (1.0 + math.exp(-0.25)),
        1.0 / (1.0 + math.exp(-0.91)),
    ])
    assert captured["url"] == "https://api.jina.ai/v1/rerank"
    assert captured["json"]["top_n"] == 2
    assert captured["json"]["return_documents"] is False
    assert captured["headers"]["Authorization"] == "Bearer test-key"


def test_jina_requires_api_key() -> None:
    provider = JinaAPIReranker(api_key="", model_name="test-model")
    provider.api_key = None

    with pytest.raises(RuntimeError, match="JINA_API_KEY"):
        provider.score("query", ["document"], [{}])


def test_jina_rejects_incomplete_scores(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.rag.reranking.jina.httpx.post",
        lambda *args, **kwargs: _FakeResponse({
            "results": [{"index": 0, "relevance_score": 0.5}]
        }),
    )
    provider = JinaAPIReranker(api_key="test-key")

    with pytest.raises(RuntimeError, match="incomplete"):
        provider.score("query", ["one", "two"], [{}, {}])


def test_jina_v2_preserves_normalized_scores(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.rag.reranking.jina.httpx.post",
        lambda *args, **kwargs: _FakeResponse({
            "results": [{"index": 0, "relevance_score": 0.42}]
        }),
    )
    provider = JinaAPIReranker(
        api_key="test-key",
        model_name="jina-reranker-v2-base-multilingual",
    )

    assert provider.score("query", ["document"], [{}]) == [0.42]
