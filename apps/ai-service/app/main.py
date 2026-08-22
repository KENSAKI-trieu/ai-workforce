from contextlib import asynccontextmanager
import json

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.base.registry import agent_registry
from app.chains.chat import generate_chat
from app.chains.structured_extraction import extract_agent_routing
from app.config import settings
from app.feature_flags import runtime_feature_snapshot, select_langchain_runtime
from app.rag.embedding.factory import get_embedding_provider
from app.rag.ingestion.chunker import chunk_document
from app.rag.reranking.reranker import rerank_with_metadata
from app.middleware.context import AgentRuntimeContext
from app.middleware.observability import GatewayTelemetrySink
from app.models.factory import configured_chat_models
from app.orchestration.decision import DeterministicDecisionProvider, LangChainDecisionProvider
from app.orchestration.engine import OrchestrationRuntimeContext
from app.orchestration.persistence import OrchestrationEngineProvider
from app.tools.gateway import ToolGatewayClient
from app.tools.registry import build_langchain_tools
from app.shared.contracts import (
    AgentRouteRequest,
    AgentRouteResponse,
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


def require_internal_token(
    x_ai_service_key: str | None = Header(default=None),
) -> None:
    expected = settings.AI_SERVICE_INTERNAL_TOKEN
    if expected and x_ai_service_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid AI service credential",
        )


def _tool_jwt(x_internal_tool_authorization: str | None) -> str:
    prefix = "Bearer "
    if not x_internal_tool_authorization or not x_internal_tool_authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Internal tool authorization is required")
    return x_internal_tool_authorization[len(prefix):].strip()


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
        if tool.name in set(allowed_tools) and tool.name not in set(denied_tools)
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
                "input_schema": tool.args_schema.model_json_schema() if tool.args_schema else {},
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


@app.post("/v1/embeddings", response_model=EmbeddingResponse, dependencies=[Depends(require_internal_token)])
async def create_embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
    provider = get_embedding_provider()
    texts = request.texts
    if request.input_type == "query":
        texts = [provider.prepare_query(text) for text in texts]
    vectors = provider.embed(texts)
    return EmbeddingResponse(
        vectors=vectors,
        token_counts=[provider.count_tokens(text) for text in texts],
        model=provider.model_name,
        version=provider.version,
        dimension=provider.dimension,
        max_input_tokens=provider.max_input_tokens,
    )


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


@app.post("/v1/agents/route", response_model=AgentRouteResponse, dependencies=[Depends(require_internal_token)])
def route_agent(request: AgentRouteRequest) -> AgentRouteResponse:
    agent = agent_registry.resolve(request.requested_role, request.message)
    runtime = select_langchain_runtime(agent.role)
    if runtime.backend == "langchain":
        decision = extract_agent_routing(
            request.message,
            {
                item.role: f"{item.description}; capabilities={', '.join(item.capabilities)}"
                for item in agent_registry.all()
            },
            fallback_role=agent.role,
        )
        agent = agent_registry.get(decision.role)
    return AgentRouteResponse(
        role=agent.role,
        agent_name=agent.name,
        capabilities=list(agent.capabilities),
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
