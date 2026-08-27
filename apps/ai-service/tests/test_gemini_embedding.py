import math

from app.rag.embedding.gemini import GeminiEmbeddingProvider


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_gemini_embedding_uses_batch_api_and_retrieval_task(monkeypatch) -> None:
    requests: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response({
            "embeddings": [
                {"values": [1.0] + [0.0] * 767}
                for _ in json["requests"]
            ]
        })

    monkeypatch.setattr("app.rag.embedding.gemini.httpx.post", fake_post)
    provider = GeminiEmbeddingProvider(
        api_key="test-key",
        model_name="gemini-embedding-001",
        version="gemini-embedding-001-v1",
        dimension=768,
        batch_size=2,
    )

    vectors = provider.embed_for_type(["one", "two", "three"], input_type="query")

    assert len(requests) == 2
    assert requests[0]["url"].endswith(
        "/models/gemini-embedding-001:batchEmbedContents"
    )
    assert requests[0]["headers"]["x-goog-api-key"] == "test-key"
    assert requests[0]["json"]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
    assert requests[0]["json"]["requests"][0]["outputDimensionality"] == 768
    assert len(vectors) == 3
    assert len(vectors[0]) == 768
    assert math.isclose(sum(value * value for value in vectors[0]), 1.0)


def test_gemini_embedding_requires_api_key() -> None:
    try:
        GeminiEmbeddingProvider(
            api_key="",
            model_name="gemini-embedding-001",
            version="gemini-embedding-001-v1",
            dimension=768,
            batch_size=8,
        )
    except RuntimeError as exc:
        assert "GOOGLE_AI_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing Gemini API key to fail")
