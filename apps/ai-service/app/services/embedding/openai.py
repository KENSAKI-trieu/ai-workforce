import httpx

from app.services.embedding.base import EmbeddingProvider


class OpenAIEmbeddingProvider(EmbeddingProvider):
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
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings")
        self.api_key = api_key
        self.model_name = model_name
        self.version = version
        self.dimension = dimension
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model_name, "input": texts, "dimensions": self.dimension},
            timeout=60,
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
        return [row["embedding"] for row in rows]
