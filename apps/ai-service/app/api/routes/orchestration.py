"""Run, stream and resume a governed agent graph for one conversation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.base.persistence import orchestration_engines
from app.agents.base.runtime import ToolContractsUnavailable, build_runtime_context, initial_state
from app.agents.registry import resolve_agent
from app.api.dependencies import require_internal_token, tool_jwt
from app.api.sse import SSE_HEADERS, encode_sse
from app.schemas.orchestration import (
    OrchestrationRequest,
    OrchestrationResponse,
    OrchestrationResumeRequest,
)

router = APIRouter()


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


def _context_for(request: OrchestrationRequest, authorization: str | None):
    # Checked before anything runs, so a stream refuses an unknown agent with a 422
    # instead of opening and failing mid-stream.
    resolve_agent(request.requested_agent)
    return build_runtime_context(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        role=request.role,
        department=request.department,
        conversation_id=request.conversation_id,
        workflow_id=request.workflow_id,
        agent_role=request.requested_agent.upper(),
        allowed_tools=request.allowed_tools,
        denied_tools=request.denied_tools,
        tool_jwt=tool_jwt(authorization),
        tenant_instructions=request.tenant_instructions,
        disabled_tools={item.name: item.label for item in request.disabled_tools},
    )


@router.post(
    "/v1/orchestration/run",
    response_model=OrchestrationResponse,
    dependencies=[Depends(require_internal_token)],
)
def run_orchestration(
    request: OrchestrationRequest,
    x_internal_tool_authorization: str | None = Header(default=None),
) -> OrchestrationResponse:
    try:
        context = _context_for(request, x_internal_tool_authorization)
        result = orchestration_engines.get().invoke(
            initial_state(request),
            context=context,
            thread_id=request.conversation_id,
        )
    except ToolContractsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Tool contracts are unavailable") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _orchestration_response(result, request.conversation_id)


@router.post(
    "/v1/orchestration/run/stream",
    dependencies=[Depends(require_internal_token)],
)
def stream_orchestration(
    request: OrchestrationRequest,
    x_internal_tool_authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Stream a sanitized orchestration protocol over SSE."""
    try:
        context = _context_for(request, x_internal_tool_authorization)
    except ToolContractsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Tool contracts are unavailable") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def event_stream():
        try:
            for item in orchestration_engines.get().stream(
                initial_state(request),
                context=context,
                thread_id=request.conversation_id,
            ):
                event = str(item.get("event") or "message")
                if event == "result":
                    response = _orchestration_response(
                        dict(item["result"]), request.conversation_id
                    )
                    yield encode_sse("result", response.model_dump(mode="json"))
                else:
                    yield encode_sse(
                        event,
                        {key: value for key, value in item.items() if key != "event"},
                    )
        except Exception as exc:
            yield encode_sse(
                "error",
                {"message": "Orchestration stream failed", "code": type(exc).__name__},
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post(
    "/v1/orchestration/resume",
    response_model=OrchestrationResponse,
    dependencies=[Depends(require_internal_token)],
)
def resume_orchestration(
    request: OrchestrationResumeRequest,
    x_internal_tool_authorization: str | None = Header(default=None),
) -> OrchestrationResponse:
    try:
        resolve_agent(request.agent_role)
        context = build_runtime_context(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            role=request.role,
            department=request.department,
            conversation_id=request.conversation_id,
            workflow_id=request.workflow_id,
            agent_role=request.agent_role.upper(),
            allowed_tools=request.allowed_tools,
            denied_tools=request.denied_tools,
            tool_jwt=tool_jwt(x_internal_tool_authorization),
            tenant_instructions=request.tenant_instructions,
            disabled_tools={item.name: item.label for item in request.disabled_tools},
        )
        result = orchestration_engines.get().resume(
            request.resume,
            context=context,
            thread_id=request.conversation_id,
        )
    except ToolContractsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Tool contracts are unavailable") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _orchestration_response(result, request.conversation_id)
