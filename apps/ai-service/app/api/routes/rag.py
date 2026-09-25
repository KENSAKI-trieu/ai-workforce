"""Chunking, embedding, token counting and reranking for the backend's RAG pipeline."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse

from app.api.dependencies import require_internal_token
from app.api.sse import SSE_HEADERS, encode_sse
from app.schemas.rag import (
    ChunkRequest,
    ChunkResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    RerankRequest,
    RerankResponse,
    TokenCountRequest,
    TokenCountResponse,
)
from app.services.chunking.chunker import chunk_document, iter_chunk_document
from app.services.embedding.factory import get_embedding_provider
from app.services.pipeline_events import pipeline_events
from app.services.reranking.reranker import rerank_with_metadata

router = APIRouter()


@router.post("/v1/rag/chunk", response_model=ChunkResponse, dependencies=[Depends(require_internal_token)])
def chunk_text(request: ChunkRequest) -> ChunkResponse:
    return ChunkResponse(chunks=chunk_document(
        request.content,
        chunk_size=request.chunk_size,
        chunk_overlap=request.chunk_overlap,
    ))


@router.post("/v1/rag/chunk/stream", dependencies=[Depends(require_internal_token)])
def stream_chunk_text(request: ChunkRequest) -> StreamingResponse:
    """Stream exact planned-segment and generated-chunk counters."""
    def events():
        last_progress = {
            "processed_segments": 0,
            "total_segments": 0,
            "remaining_segments": 0,
            "chunks_created": 0,
        }
        for progress in iter_chunk_document(
            request.content,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
        ):
            last_progress = {
                key: progress[key]
                for key in last_progress
            }
            if request.progress_stream_id:
                if request.progress_total_count is None:
                    processed_segments = (
                        request.progress_completed_before
                        + int(progress["processed_segments"])
                    )
                    total_segments = (
                        request.progress_completed_before
                        + int(progress["total_segments"])
                    )
                else:
                    segment_complete = int(progress["remaining_segments"]) == 0
                    processed_segments = (
                        request.progress_completed_before + int(segment_complete)
                    )
                    total_segments = request.progress_total_count
                pipeline_events.publish(request.progress_stream_id, {
                    "processing_status": "chunking",
                    "processing_progress": (
                        round((processed_segments / total_segments) * 100)
                        if total_segments
                        else 100
                    ),
                    "chunk_segments_processed": processed_segments,
                    "chunk_segments_total": total_segments,
                    "chunk_segments_remaining": max(total_segments - processed_segments, 0),
                    "chunks_created": (
                        request.progress_chunks_before
                        + int(progress["chunks_created"])
                    ),
                })
            yield (
                "event: progress\n"
                f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"
            )
        yield (
            "event: result\n"
            f"data: {json.dumps(last_progress, ensure_ascii=False)}\n\n"
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/v1/pipeline/events/{stream_id}")
def stream_pipeline_events(
    stream_id: str = Path(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
) -> StreamingResponse:
    """Expose counter-only progress directly to an authorized upload browser."""
    def events():
        sequence = 0
        while True:
            pending = pipeline_events.wait_after(stream_id, sequence, timeout=15.0)
            if not pending:
                yield ": keep-alive\n\n"
                continue
            for event in pending:
                sequence = int(event["event_sequence"])
                yield encode_sse("progress", event)
                if (
                    event.get("processing_status") == "embedding"
                    and int(event.get("embedding_remaining_chunks", 1)) == 0
                ):
                    return

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/v1/embeddings", response_model=EmbeddingResponse, dependencies=[Depends(require_internal_token)])
async def create_embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
    provider = get_embedding_provider()
    texts = request.texts
    vectors = provider.embed_for_type(texts, input_type=request.input_type)
    total_count = max(request.total_count or len(texts), request.completed_before + len(texts))
    embedded_count = min(request.completed_before + len(vectors), total_count)
    response = EmbeddingResponse(
        vectors=vectors,
        token_counts=[provider.count_tokens(text) for text in texts],
        model=provider.model_name,
        version=provider.version,
        dimension=provider.dimension,
        max_input_tokens=provider.max_input_tokens,
        batch_count=len(vectors),
        embedded_count=embedded_count,
        total_count=total_count,
        remaining_count=max(total_count - embedded_count, 0),
    )
    if request.progress_stream_id:
        pipeline_events.publish(request.progress_stream_id, {
            "processing_status": "embedding",
            "processing_progress": round((embedded_count / total_count) * 100),
            "embedded_chunks": embedded_count,
            "embedding_total_chunks": total_count,
            "embedding_remaining_chunks": max(total_count - embedded_count, 0),
            "embedding_batch_count": len(vectors),
        })
    return response


@router.post("/v1/token-count", response_model=TokenCountResponse, dependencies=[Depends(require_internal_token)])
def count_tokens(request: TokenCountRequest) -> TokenCountResponse:
    provider = get_embedding_provider()
    return TokenCountResponse(
        token_counts=[provider.count_tokens(text) for text in request.texts],
        max_input_tokens=provider.max_input_tokens,
    )


@router.post("/v1/rag/rerank", response_model=RerankResponse, dependencies=[Depends(require_internal_token)])
def rerank(request: RerankRequest) -> RerankResponse:
    outcome = rerank_with_metadata(
        request.query,
        request.candidates,
        top_k=request.top_k,
    )
    return RerankResponse(
        results=outcome.results,
        backend=outcome.backend,
        model=outcome.model,
        fallback_used=outcome.fallback_used,
        candidates_scored=outcome.candidates_scored,
        latency_ms=outcome.latency_ms,
    )
