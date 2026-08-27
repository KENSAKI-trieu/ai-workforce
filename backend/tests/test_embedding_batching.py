from app.services.embedding_service import EmbeddingService


class FakeAIClient:
    enabled = True

    def __init__(self) -> None:
        self.token_batches: list[list[str]] = []
        self.embedding_batches: list[list[str]] = []

    def count_tokens(self, texts: list[str]) -> dict:
        self.token_batches.append(list(texts))
        return {
            "max_input_tokens": 2048,
            "token_counts": [len(text.split()) for text in texts],
        }

    def embed(self, texts: list[str]) -> dict:
        self.embedding_batches.append(list(texts))
        return {
            "dimension": 3,
            "vectors": [[float(index), 0.0, 0.0] for index, _ in enumerate(texts)],
        }


def test_remote_embedding_service_batches_large_requests(monkeypatch) -> None:
    client = FakeAIClient()
    monkeypatch.setattr(
        "app.services.embedding_service.get_ai_service_client",
        lambda: client,
    )
    service = EmbeddingService()
    service.batch_size = 2
    service.dimension = 3
    texts = [f"text {index}" for index in range(5)]

    assert service.count_tokens_batch(texts) == [2, 2, 2, 2, 2]
    assert len(service.embed_texts(texts)) == 5
    assert [len(batch) for batch in client.token_batches] == [2, 2, 1]
    assert [len(batch) for batch in client.embedding_batches] == [2, 2, 1]
