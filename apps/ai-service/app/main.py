from contextlib import asynccontextmanager
import hmac
import json
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Path, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.chains.chat import generate_chat
from app.config import settings
from app.feature_flags import runtime_feature_snapshot
from app.guardrails.tool_permission import is_tool_allowed
from app.rag.embedding.factory import get_embedding_provider
from app.rag.ingestion.chunker import chunk_document, iter_chunk_document
from app.rag.reranking.reranker import rerank_with_metadata
from app.middleware.context import AgentRuntimeContext
from app.middleware.observability import GatewayTelemetrySink
from app.models.factory import configured_chat_models
from app.orchestration.decision import DeterministicDecisionProvider, LangChainDecisionProvider
from app.orchestration.engine import OrchestrationRuntimeContext
from app.orchestration.persistence import OrchestrationEngineProvider
from app.pipeline_events import pipeline_events
from app.tools.gateway import ToolGatewayClient
from app.tools.registry import build_langchain_tools
from app.shared.contracts import (
    ChunkRequest,
    ChunkResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMGenerateRequest,
    LLMGenerateResponse,
    RerankRequest,
    RerankResponse,
    RuntimeFeatureResponse,
    OrchestrationRequest,
    OrchestrationResumeRequest,
    OrchestrationResponse,
    TokenCountRequest,
    TokenCountResponse,
)


orchestration_engines = OrchestrationEngineProvider()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Every /v1 endpoint here answers with company data or spends money on a model
    # provider. A missing credential used to disable the check instead of failing the
    # request, so a deployment that forgot the variable served the whole surface to
    # anyone who could reach the port -- and nothing in the logs said so.
    if not settings.AI_SERVICE_INTERNAL_TOKEN and settings.APP_ENV != "test":
        raise RuntimeError(
            "AI_SERVICE_INTERNAL_TOKEN is not set. The AI service refuses to start "
            "without it because every /v1 endpoint would be reachable unauthenticated."
        )
    orchestration_engines.start()
    try:
        if settings.EMBEDDING_PRELOAD:
            # Load and warm up the local model on the main thread. On Windows,
            # initializing CUDA from FastAPI's worker thread can terminate the
            # process without producing a Python traceback.
            get_embedding_provider().embed(["embedding warmup"])
        yield
    finally:
        orchestration_engines.close()


app = FastAPI(
    title="AI Workforce AI Service",
    version="1.0.0",
    description="Durable agent orchestration, RAG, embedding and prompt runtime.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.FRONTEND_URL.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept"],
)


def require_internal_token(
    x_ai_service_key: str | None = Header(default=None),
) -> None:
    expected = settings.AI_SERVICE_INTERNAL_TOKEN
    if not expected:
        # Startup already refuses this outside tests. Answering 503 rather than letting
        # the request through keeps the missing-configuration case closed everywhere.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service credential is not configured",
        )
    if not x_ai_service_key or not hmac.compare_digest(x_ai_service_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid AI service credential",
        )


def _tool_jwt(x_internal_tool_authorization: str | None) -> str:
    prefix = "Bearer "
    if not x_internal_tool_authorization or not x_internal_tool_authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Internal tool authorization is required")
    return x_internal_tool_authorization[len(prefix):].strip()


# Fields the orchestrator injects from the trusted runtime context. The system prompt
# already tells the model not to produce them; leaving them in the advertised schema also
# marked them required, so a compliant answer looked incomplete against its own contract.
SERVER_INJECTED_TOOL_FIELDS = ("tenant_id", "audit")


def _model_facing_schema(tool: Any) -> dict[str, Any]:
    if tool.args_schema is None:
        return {}
    schema = dict(tool.args_schema.model_json_schema())
    properties = {
        name: value
        for name, value in (schema.get("properties") or {}).items()
        if name not in SERVER_INJECTED_TOOL_FIELDS
    }
    schema["properties"] = properties
    required = [
        name for name in (schema.get("required") or [])
        if name not in SERVER_INJECTED_TOOL_FIELDS
    ]
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)
    return schema


def _orchestration_context(
    *,
    tenant_id: str,
    user_id: str,
    role: str,
    department: str,
    conversation_id: str,
    workflow_id: str,
    agent_role: str,
    allowed_tools: list[str],
    denied_tools: list[str],
    tool_jwt: str,
) -> OrchestrationRuntimeContext:
    gateway = ToolGatewayClient(settings.BACKEND_TOOL_GATEWAY_URL, tool_jwt)
    tools = {
        tool.name: tool
        for tool in build_langchain_tools(gateway)
        if is_tool_allowed(tool.name, allowed_tools, denied_tools)
    }
    security = AgentRuntimeContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        department=department,
        agent_role=agent_role,
        correlation_id=conversation_id,
        conversation_id=conversation_id,
        workflow_id=workflow_id,
        allowed_tools=frozenset(tools),
        denied_tools=frozenset(denied_tools),
    )
    models = configured_chat_models(max_retries=0)
    if models:
        contracts = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": _model_facing_schema(tool),
                "action": (tool.metadata or {}).get("action"),
            }
            for tool in tools.values()
        ]
        decision_provider = LangChainDecisionProvider(
            model=models[0],
            fallback_models=models[1:],
            telemetry_sink=GatewayTelemetrySink(gateway),
            tool_contracts=contracts,
            runtime_context=security,
        )
    else:
        decision_provider = DeterministicDecisionProvider()
    return OrchestrationRuntimeContext(
        security=security,
        decision_provider=decision_provider,
        tools=tools,
        approval_registrar=gateway.create_graph_approval,
    )


def _orchestration_response(result: dict, conversation_id: str) -> OrchestrationResponse:
    raw_interrupts = result.pop("__interrupt__", ())
    interrupts = [
        {"id": getattr(item, "id", None), "value": getattr(item, "value", item)}
        for item in raw_interrupts
    ]
    return OrchestrationResponse(
        thread_id=conversation_id,
        status="AWAITING_APPROVAL" if interrupts else "COMPLETED",
        state=result,
        interrupts=interrupts,
    )


def _initial_orchestration_state(request: OrchestrationRequest) -> dict:
    return {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "role": request.role,
        "department": request.department,
        "conversation_id": request.conversation_id,
        "workflow_id": request.workflow_id,
        "messages": [{"role": "user", "content": request.message}],
        "intent": "",
        "selected_agent": "",
        "retrieved_context": [],
        "tool_calls": [],
        "citations": [],
        "approval_id": None,
        "final_answer": None,
        "errors": [],
        "requested_agent": request.requested_agent,
    }


def _encode_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "ai-service", "version": "1.0.0"}


@app.get(
    "/health/runtime",
    response_model=RuntimeFeatureResponse,
    dependencies=[Depends(require_internal_token)],
)
def runtime_health() -> RuntimeFeatureResponse:
    return RuntimeFeatureResponse.model_validate(runtime_feature_snapshot())


@app.get("/health/accelerator", dependencies=[Depends(require_internal_token)])
def accelerator_health() -> dict[str, object]:
    try:
        import torch
    except ImportError:
        return {
            "cuda_available": False,
            "torch_version": None,
            "cuda_runtime": None,
            "device_count": 0,
            "device_name": None,
            "embedding_device": settings.EMBEDDING_DEVICE,
            "rerank_device": settings.RERANK_DEVICE,
            "reason": "PyTorch is not installed",
        }
    available = torch.cuda.is_available()
    return {
        "cuda_available": available,
        "torch_version": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "device_count": torch.cuda.device_count(),
        "device_name": torch.cuda.get_device_name(0) if available else None,
        "embedding_device": settings.EMBEDDING_DEVICE,
        "rerank_device": settings.RERANK_DEVICE,
    }


@app.post("/v1/rag/chunk", response_model=ChunkResponse, dependencies=[Depends(require_internal_token)])
def chunk_text(request: ChunkRequest) -> ChunkResponse:
    return ChunkResponse(chunks=chunk_document(
        request.content,
        chunk_size=request.chunk_size,
        chunk_overlap=request.chunk_overlap,
    ))


@app.post("/v1/rag/chunk/stream", dependencies=[Depends(require_internal_token)])
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


@app.get("/v1/pipeline/events/{stream_id}")
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
                yield _encode_sse("progress", event)
                if (
                    event.get("processing_status") == "embedding"
                    and int(event.get("embedding_remaining_chunks", 1)) == 0
                ):
                    return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/v1/embeddings", response_model=EmbeddingResponse, dependencies=[Depends(require_internal_token)])
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


@app.post("/v1/token-count", response_model=TokenCountResponse, dependencies=[Depends(require_internal_token)])
def count_tokens(request: TokenCountRequest) -> TokenCountResponse:
    provider = get_embedding_provider()
    return TokenCountResponse(
        token_counts=[provider.count_tokens(text) for text in request.texts],
        max_input_tokens=provider.max_input_tokens,
    )


@app.post("/v1/rag/rerank", response_model=RerankResponse, dependencies=[Depends(require_internal_token)])
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


@app.post(
    "/v1/orchestration/run",
    response_model=OrchestrationResponse,
    dependencies=[Depends(require_internal_token)],
)
def run_orchestration(
    request: OrchestrationRequest,
    x_internal_tool_authorization: str | None = Header(default=None),
) -> OrchestrationResponse:
    agent_role = (request.requested_agent or "KNOWLEDGE").upper()
    try:
        context = _orchestration_context(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            role=request.role,
            department=request.department,
            conversation_id=request.conversation_id,
            workflow_id=request.workflow_id,
            agent_role=agent_role,
            allowed_tools=request.allowed_tools,
            denied_tools=request.denied_tools,
            tool_jwt=_tool_jwt(x_internal_tool_authorization),
        )
        result = orchestration_engines.get().invoke(
            _initial_orchestration_state(request),
            context=context,
            thread_id=request.conversation_id,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _orchestration_response(result, request.conversation_id)


@app.post(
    "/v1/orchestration/run/stream",
    dependencies=[Depends(require_internal_token)],
)
def stream_orchestration(
    request: OrchestrationRequest,
    x_internal_tool_authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Stream a sanitized orchestration protocol over SSE."""
    agent_role = (request.requested_agent or "KNOWLEDGE").upper()
    try:
        context = _orchestration_context(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            role=request.role,
            department=request.department,
            conversation_id=request.conversation_id,
            workflow_id=request.workflow_id,
            agent_role=agent_role,
            allowed_tools=request.allowed_tools,
            denied_tools=request.denied_tools,
            tool_jwt=_tool_jwt(x_internal_tool_authorization),
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def event_stream():
        try:
            for item in orchestration_engines.get().stream(
                _initial_orchestration_state(request),
                context=context,
                thread_id=request.conversation_id,
            ):
                event = str(item.get("event") or "message")
                if event == "result":
                    response = _orchestration_response(
                        dict(item["result"]), request.conversation_id
                    )
                    yield _encode_sse("result", response.model_dump(mode="json"))
                else:
                    yield _encode_sse(
                        event,
                        {key: value for key, value in item.items() if key != "event"},
                    )
        except Exception as exc:
            yield _encode_sse(
                "error",
                {"message": "Orchestration stream failed", "code": type(exc).__name__},
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post(
    "/v1/orchestration/resume",
    response_model=OrchestrationResponse,
    dependencies=[Depends(require_internal_token)],
)
def resume_orchestration(
    request: OrchestrationResumeRequest,
    x_internal_tool_authorization: str | None = Header(default=None),
) -> OrchestrationResponse:
    try:
        context = _orchestration_context(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            role=request.role,
            department=request.department,
            conversation_id=request.conversation_id,
            workflow_id=request.workflow_id,
            agent_role=request.agent_role.upper(),
            allowed_tools=request.allowed_tools,
            denied_tools=request.denied_tools,
            tool_jwt=_tool_jwt(x_internal_tool_authorization),
        )
        result = orchestration_engines.get().resume(
            request.resume,
            context=context,
            thread_id=request.conversation_id,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _orchestration_response(result, request.conversation_id)


@app.post("/v1/llm/generate", response_model=LLMGenerateResponse, dependencies=[Depends(require_internal_token)])
def generate_text(request: LLMGenerateRequest) -> LLMGenerateResponse:
    result = generate_chat(
        request.messages,
        provider=request.provider,
        model=request.model,
    )
    return LLMGenerateResponse(
        content=result.content,
        provider=result.provider,
        model=result.model,
        usage=result.usage,
    )
