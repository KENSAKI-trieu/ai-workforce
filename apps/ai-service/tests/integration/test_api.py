import asyncio
import json
import math

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import Settings
from app.api.routes.health import runtime_feature_snapshot
from app.services.embedding.factory import get_embedding_provider
from evaluation.metrics.reranking import ndcg_at_k, reciprocal_rank
from app.services.chunking.chunker import chunk_document
from app.services.reranking.base import BaseReranker
from app.services.reranking.reranker import RerankPipeline


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "ai-service"


def test_runtime_health_defaults_to_legacy() -> None:
    response = client.get("/health/runtime")
    assert response.status_code == 200
    payload = response.json()
    assert payload["active_runtime"] == "legacy"
    assert payload["legacy_fallback"] is True
    assert "langchain" not in payload
    assert payload["langgraph"]["effective"] is False


@pytest.mark.internal_auth
def test_internal_health_requires_configured_token(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_SERVICE_INTERNAL_TOKEN", "phase-1-secret")
    assert client.get("/health/runtime").status_code == 401
    assert client.get(
        "/health/runtime",
        headers={"X-AI-Service-Key": "wrong-secret"},
    ).status_code == 401
    response = client.get(
        "/health/runtime",
        headers={"X-AI-Service-Key": "phase-1-secret"},
    )
    assert response.status_code == 200


@pytest.mark.internal_auth
def test_internal_endpoint_is_closed_when_token_is_unset(monkeypatch) -> None:
    """A missing credential must not read as "no authentication required"."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_SERVICE_INTERNAL_TOKEN", None)
    assert client.get("/health/runtime").status_code == 503
    assert client.get(
        "/health/runtime",
        headers={"X-AI-Service-Key": "anything"},
    ).status_code == 503


@pytest.mark.internal_auth
def test_startup_refuses_to_run_without_internal_token(monkeypatch) -> None:
    from app.core.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "AI_SERVICE_INTERNAL_TOKEN", None)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(RuntimeError, match="AI_SERVICE_INTERNAL_TOKEN"):
        asyncio.run(_enter_lifespan(lifespan))


async def _enter_lifespan(lifespan) -> None:
    async with lifespan(app):
        pass


def test_runtime_health_reports_enabled_langgraph() -> None:
    config = Settings(LANGGRAPH_ENABLED=True)
    snapshot = runtime_feature_snapshot(config)
    assert snapshot["active_runtime"] == "langgraph"
    assert snapshot["langgraph"]["effective"] is True


def test_accelerator_health_contract() -> None:
    response = client.get("/health/accelerator")
    assert response.status_code == 200
    payload = response.json()
    assert "cuda_available" in payload
    assert "torch_version" in payload


def test_business_boundary_chunking() -> None:
    chunks = chunk_document(
        "# Chính sách nghỉ phép\n"
        "Điều 1. Phạm vi\nÁp dụng toàn công ty.\n"
        "Khoản 1. Điều kiện\nNhân viên còn ngày phép.\n"
        "Bước 1: Gửi yêu cầu\nNhân viên tạo đơn."
    )
    assert [chunk["section_type"] for chunk in chunks] == [
        "heading", "article", "clause", "step"
    ]


def test_chunk_endpoint_contract() -> None:
    response = client.post("/v1/rag/chunk", json={"content": "## Quy trình\nNội dung."})
    assert response.status_code == 200
    chunk = response.json()["chunks"][0]
    assert chunk["section_title"] == "Quy trình"
    assert chunk["token_count"] == 5


def test_chunk_stream_reports_remaining_segments_and_created_chunks() -> None:
    response = client.post("/v1/rag/chunk/stream", json={
        "content": "# Phần một\nNội dung một.\n# Phần hai\nNội dung hai.",
    })

    assert response.status_code == 200
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    progress = payloads[:-1]
    assert [item["processed_segments"] for item in progress] == [1, 2]
    assert [item["remaining_segments"] for item in progress] == [1, 0]
    assert progress[-1]["chunks_created"] == 2


def test_embedding_endpoint_contract() -> None:
    response = client.post("/v1/embeddings", json={
        "texts": ["chính sách nghỉ phép", "quy trình phê duyệt"],
        "input_type": "query",
        "completed_before": 2,
        "total_count": 5,
    })
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["vectors"]) == 2
    assert all(len(vector) == payload["dimension"] for vector in payload["vectors"])
    assert len(payload["token_counts"]) == 2
    assert payload["model"]
    assert payload["version"]
    assert payload["batch_count"] == 2
    assert payload["embedded_count"] == 4
    assert payload["total_count"] == 5
    assert payload["remaining_count"] == 1


def test_browser_pipeline_stream_replays_chunk_and_embedding_progress() -> None:
    stream_id = "browser-progress-contract-001"
    chunk_response = client.post("/v1/rag/chunk/stream", json={
        "content": "# One\nFirst section.\n# Two\nSecond section.",
        "progress_stream_id": stream_id,
    })
    assert chunk_response.status_code == 200
    embedding_response = client.post("/v1/embeddings", json={
        "texts": ["first", "second"],
        "completed_before": 0,
        "total_count": 2,
        "progress_stream_id": stream_id,
    })
    assert embedding_response.status_code == 200

    response = client.get(
        f"/v1/pipeline/events/{stream_id}",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert payloads[0]["processing_status"] == "chunking"
    assert payloads[-1]["processing_status"] == "embedding"
    assert payloads[-1]["embedded_chunks"] == 2
    assert payloads[-1]["embedding_remaining_chunks"] == 0
    assert [item["event_sequence"] for item in payloads] == list(
        range(1, len(payloads) + 1)
    )


def test_token_count_endpoint_contract() -> None:
    response = client.post("/v1/token-count", json={"texts": ["one two", "three"]})
    assert response.status_code == 200
    assert response.json()["token_counts"] == [2, 1]


def test_llm_generate_local_contract() -> None:
    response = client.post("/v1/llm/generate", json={
        "provider": "local",
        "messages": [{"role": "user", "content": "baseline contract"}],
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == "Local provider received: baseline contract"
    assert payload["provider"] == "local"
    assert payload["model"] == "local-deterministic"
    assert payload["usage"]["prompt_tokens"] == 2


def test_deterministic_embedding_contract() -> None:
    provider = get_embedding_provider()
    vectors = provider.embed(["same text", "same text"])
    assert vectors[0] == vectors[1]
    assert len(vectors[0]) == provider.dimension
    assert math.isclose(sum(value * value for value in vectors[0]), 1.0, rel_tol=1e-6)


def test_agent_route_endpoint_is_gone() -> None:
    # The backend always names the agent, so the AI service no longer guesses one.
    assert client.post("/v1/agents/route", json={"message": "Kiểm tra ngân sách tháng"}).status_code == 404


class _FixedBGEReranker(BaseReranker):
    backend = "bge"
    model_name = "BAAI/bge-reranker-v2-m3"

    def score(self, query, documents, candidates):
        return [0.95, 0.30]


class _FailingReranker(BaseReranker):
    backend = "bge"
    model_name = "unavailable-bge"

    def score(self, query, documents, candidates):
        raise RuntimeError("model unavailable")


def _rerank_candidates() -> list[dict]:
    return [
        {
            "id": "leave",
            "document_title": "Chính sách nghỉ phép",
            "section_title": "Số ngày phép",
            "content": "Nhân viên có 12 ngày nghỉ phép mỗi năm.",
            "_dense_score": 0.80,
            "_sparse_score": 1.0,
            "_rrf_score": 1.0,
        },
        {
            "id": "travel",
            "content": "Quy định thanh toán công tác phí.",
            "_dense_score": 0.40,
            "_sparse_score": 0.1,
            "_rrf_score": 0.5,
        },
        {
            "id": "leave",
            "content": "Bản trùng không được rerank lần hai.",
        },
    ]



def test_bge_rerank_pipeline_contract() -> None:
    outcome = RerankPipeline(provider=_FixedBGEReranker()).run(
        "Tôi có bao nhiêu ngày nghỉ phép?",
        _rerank_candidates(),
        top_k=2,
    )
    assert outcome.candidates_scored == 2
    assert outcome.fallback_used is False
    assert outcome.model == "BAAI/bge-reranker-v2-m3"
    assert [item["id"] for item in outcome.results] == ["leave", "travel"]
    assert outcome.results[0]["rerank_model_score"] == 0.95



def test_rerank_pipeline_falls_back_to_lexical() -> None:
    outcome = RerankPipeline(provider=_FailingReranker()).run(
        "nghỉ phép 12 ngày",
        _rerank_candidates(),
        top_k=1,
    )
    assert outcome.fallback_used is True
    assert outcome.backend == "lexical"
    assert outcome.results[0]["id"] == "leave"


def test_rerank_endpoint_observability_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.rag.rerank_with_metadata",
        lambda query, candidates, top_k: RerankPipeline(
            provider=_FixedBGEReranker()
        ).run(query, candidates, top_k=top_k),
    )
    response = client.post("/v1/rag/rerank", json={
        "query": "nghỉ phép 12 ngày",
        "candidates": _rerank_candidates(),
        "top_k": 1,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] in {"bge", "lexical"}
    assert payload["candidates_scored"] == 2
    assert payload["fallback_used"] is False


def test_reranking_metrics() -> None:
    assert reciprocal_rank(["wrong", "right"], {"right"}) == 0.5
    assert math.isclose(
        ndcg_at_k(["a", "b"], {"a": 3.0, "b": 1.0}, 2),
        1.0,
    )
