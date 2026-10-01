from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.embedding.base import EmbeddingProvider

AI_SERVICE_COPY = Path(__file__).resolve().parents[2] / "app" / "services" / "chunking" / "rag_chunking.py"
BACKEND_COPY = (
    Path(__file__).resolve().parents[4] / "backend" / "app" / "domains" / "knowledge" / "rag_chunking.py"
)


@pytest.mark.skipif(not BACKEND_COPY.exists(), reason="backend sources are not checked out")
def test_ai_service_and_backend_chunk_with_the_same_code() -> None:
    assert AI_SERVICE_COPY.read_bytes().replace(b"\r\n", b"\n") == BACKEND_COPY.read_bytes().replace(b"\r\n", b"\n")


def test_small_points_merge_unless_the_backend_asks_for_every_section() -> None:
    content = "Điều 5. Thanh toán\n1. Bên A thanh toán.\n2. Bên B xuất hóa đơn."
    with TestClient(app) as client:
        merged = client.post("/v1/rag/chunk", json={"content": content}).json()["chunks"]
        separate = client.post("/v1/rag/chunk", json={"content": content, "min_chunk_size": 0}).json()["chunks"]

    assert [chunk["section_title"] for chunk in merged] == ["Điều 5. Thanh toán"]
    assert [chunk["header_path"] for chunk in separate][1] == ["Điều 5. Thanh toán", "1. Bên A thanh toán."]


def test_providers_without_a_tokenizer_estimate_tokens_instead_of_counting_words() -> None:
    class NoTokenizer(EmbeddingProvider):
        def embed(self, texts: list[str]) -> list[list[float]]:
            return []

    sentence = "Người lao động được nghỉ phép năm hưởng nguyên lương."
    assert NoTokenizer().count_tokens(sentence) > len(sentence.split())
