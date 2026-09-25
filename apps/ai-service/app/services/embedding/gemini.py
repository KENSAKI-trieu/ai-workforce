import math

import httpx

from app.services.embedding.base import EmbeddingProvider


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Gemini batch embedding client for retrieval documents and queries."""

    api_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        version: str,
        dimension: int,
        batch_size: int,
    ) -> None:
        if not api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY is required for Gemini embeddings")
        self.api_key = api_key
        self.model_name = model_name.removeprefix("models/")
        self.version = version
        self.dimension = dimension
        self.batch_size = batch_size

    @property
    def max_input_tokens(self) -> int:
        return 2048

    def _normalize(self, values: list[float]) -> list[float]:
        if len(values) != self.dimension:
            raise RuntimeError(
                f"Gemini returned {len(values)} dimensions; expected {self.dimension}"
            )
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else values

    def _embed_batch(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        model = f"models/{self.model_name}"
        response = httpx.post(
            f"{self.api_url}/{model}:batchEmbedContents",
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "requests": [
                    {
                        "model": model,
                        "content": {"parts": [{"text": text}]},
                        "taskType": task_type,
                        "outputDimensionality": self.dimension,
                    }
                    for text in texts
                ]
            },
            timeout=60,
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings", [])
        if len(embeddings) != len(texts):
            raise RuntimeError("Gemini embedding result count does not match input count")
        return [self._normalize(row["values"]) for row in embeddings]

    def embed_for_type(
        self,
        texts: list[str],
        *,
        input_type: str = "document",
    ) -> list[list[float]]:
        task_type = "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(
                self._embed_batch(texts[start : start + self.batch_size], task_type=task_type)
            )
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.embed_for_type(texts, input_type="document")
