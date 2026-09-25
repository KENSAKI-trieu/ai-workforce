import hashlib
import math

from app.services.embedding.base import EmbeddingProvider


class DeterministicEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, dimension: int, version: str, batch_size: int = 16) -> None:
        self.dimension = dimension
        self.version = version
        self.batch_size = batch_size
        self.model_name = f"deterministic-hash-{dimension}"

    def _embed_one(self, text: str) -> list[float]:
        text_bytes = text.encode("utf-8")
        vector: list[float] = []
        for index in range(self.dimension):
            digest = hashlib.sha256(text_bytes + index.to_bytes(4, "big")).digest()
            value = (int.from_bytes(digest[:4], "big") / (2**32 - 1)) * 2.0 - 1.0
            vector.append(value)
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]
