"""Private gateway used by the AI service to execute governed tools."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_internal_tool_token
from app.models.models import AIAgent, AgentWorkflow, AuditLog, User
from app.services.audit_events import add_audit_event
from app.services.audit_service import log_llm_cost
from app.tools.registry import ToolAction, tool_registry
from app.services.langgraph_approvals import GRAPH_WORKFLOW_KIND, ensure_graph_approval

router = APIRouter(prefix="/internal/tools", tags=["Internal Tool Gateway"])
internal_bearer = HTTPBearer(auto_error=False)


class ToolInvocationRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class ToolInvocationResponse(BaseModel):
    tool_name: str
    action: ToolAction
    correlation_id: str
    result: Any


class ModelUsageRequest(BaseModel):
    tenant_id: UUID
    correlation_id: UUID
    agent_role: str = Field(min_length=1, max_length=50)
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=100)
    prompt_tokens: int = Field(ge=0)
    cached_prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0, le=3_600_000)
    status: str = Field(default="SUCCESS", pattern="^(SUCCESS|FAILED)$")

    @model_validator(mode="after")
    def validate_cached_tokens(self) -> "ModelUsageRequest":
        if self.cached_prompt_tokens > self.prompt_tokens:
            raise ValueError("cached_prompt_tokens cannot exceed prompt_tokens")
        return self


class GraphApprovalRegistrationRequest(BaseModel):
    tenant_id: UUID
    workflow_id: UUID
    conversation_id: UUID
    interrupt_id: str = Field(min_length=1, max_length=255)
    agent_role: str = Field(min_length=1, max_length=50)
    tool_name: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=50)
    reason: str | None = Field(default=None, max_length=1000)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _internal_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(internal_bearer),
    db: Session = Depends(get_db),
) -> tuple[User, dict[str, Any]]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Internal tool credential required")
    claims = decode_internal_tool_token(credentials.credentials)
    try:
        user_id = UUID(claims["sub"])
        tenant_id = UUID(claims["tenant_id"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid internal tool credential") from exc
    user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == tenant_id,
        User.is_active.is_(True),
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="Internal tool actor is unavailable")
    return user, claims


def _enforce_agent_configuration(
    db: Session,
    actor: User,
    claims: dict[str, Any],
    tool_name: str,
) -> None:
    role = claims.get("agent_role")
    if not role:
        return
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == actor.tenant_id,
        AIAgent.role_code == str(role).upper(),
        AIAgent.is_active.is_(True),
    ).first()
    if not agent:
        raise HTTPException(status_code=403, detail="AI Employee is unavailable")
    allowed = set(agent.tools_access or [])
    permitted_actions = set(agent.allowed_actions or [])
    denied = set(agent.disallowed_actions or [])
    if tool_name in denied or tool_name not in allowed or (
        permitted_actions and tool_name not in permitted_actions
    ):
        raise HTTPException(status_code=403, detail=f"AI Employee cannot use '{tool_name}'")


def _audit_payload(validated: Any, definition: Any) -> dict[str, Any]:
    return {
        "correlation_id": str(validated.audit.correlation_id),
        "conversation_id": str(validated.audit.conversation_id) if validated.audit.conversation_id else None,
        "workflow_id": str(validated.audit.workflow_id) if validated.audit.workflow_id else None,
        "idempotency_key": validated.audit.idempotency_key,
        "action_class": definition.action.value,
        "input_fields": sorted(validated.model_fields_set - {"tenant_id", "audit"}),
    }


def _idempotent_result(
    db: Session,
    actor: User,
    tool_name: str,
    idempotency_key: str | None,
) -> Any | None:
    if not idempotency_key:
        return None
    event = db.query(AuditLog).filter(
        AuditLog.tenant_id == actor.tenant_id,
        AuditLog.actor_user_id == actor.id,
        AuditLog.tool_name == tool_name,
        AuditLog.status == "SUCCESS",
        AuditLog.input_parameters["idempotency_key"].astext == idempotency_key,
    ).order_by(AuditLog.created_at.desc()).first()
    output = event.output_result if event else None
    return output.get("result") if isinstance(output, dict) else None


@router.get("", summary="List authoritative backend tool contracts")
def list_tools(
    actor_and_claims: tuple[User, dict[str, Any]] = Depends(_internal_actor),
) -> list[dict[str, Any]]:
    actor, _ = actor_and_claims
    return [definition.public_metadata() for definition in tool_registry.all() if definition.acl.permits(actor)]


@router.post("/model-usage", summary="Persist authoritative model usage and latency")
def record_model_usage(
    usage: ModelUsageRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    actor_and_claims: tuple[User, dict[str, Any]] = Depends(_internal_actor),
) -> dict[str, Any]:
    actor, claims = actor_and_claims
    if usage.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    authoritative_agent_role = str(claims.get("agent_role") or "").upper()
    if not authoritative_agent_role or authoritative_agent_role != usage.agent_role.upper():
        raise HTTPException(status_code=403, detail="Agent role mismatch")
    if usage.status == "FAILED":
        add_audit_event(
            db,
            tenant_id=actor.tenant_id,
            actor_user=actor,
            actor_type="AGENT",
            agent_role=authoritative_agent_role,
            action="model.call.failed",
            tool_name="model_usage_middleware",
            resource_type="MODEL_CALL",
            input_parameters={
                "correlation_id": str(usage.correlation_id),
                "provider": usage.provider,
                "model": usage.model,
            },
            output_result={"tokens_available": False},
            request=http_request,
            status="FAILED",
            error_message="Provider call failed before usage metadata was returned",
            execution_time_ms=usage.latency_ms,
        )
        db.commit()
        return {
            "usage_id": None,
            "estimated_cost_usd": None,
            "latency_ms": usage.latency_ms,
        }
    try:
        cost_log = log_llm_cost(
            db,
            actor.tenant_id,
            authoritative_agent_role,
            usage.model,
            usage.prompt_tokens,
            usage.completion_tokens,
            user_id=actor.id,
            department=actor.department,
            cached_prompt_tokens=usage.cached_prompt_tokens,
            usage_source="PROVIDER",
        )
        estimated_cost = float(cost_log.estimated_cost_usd)
        add_audit_event(
            db,
            tenant_id=actor.tenant_id,
            actor_user=actor,
            actor_type="AGENT",
            agent_role=authoritative_agent_role,
            action="model.usage.record",
            tool_name="model_usage_middleware",
            resource_type="MODEL_CALL",
            resource_id=str(cost_log.id),
            input_parameters={
                "correlation_id": str(usage.correlation_id),
                "provider": usage.provider,
                "model": usage.model,
            },
            output_result={
                "prompt_tokens": usage.prompt_tokens,
                "cached_prompt_tokens": usage.cached_prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "estimated_cost_usd": estimated_cost,
            },
            request=http_request,
            status=usage.status,
            execution_time_ms=usage.latency_ms,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "usage_id": str(cost_log.id),
        "estimated_cost_usd": estimated_cost,
        "latency_ms": usage.latency_ms,
    }


@router.post("/langgraph-approvals", summary="Persist an idempotent LangGraph interrupt approval")
def register_langgraph_approval(
    registration: GraphApprovalRegistrationRequest,
    db: Session = Depends(get_db),
    actor_and_claims: tuple[User, dict[str, Any]] = Depends(_internal_actor),
) -> dict[str, Any]:
    actor, claims = actor_and_claims
    if registration.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    claimed_role = str(claims.get("agent_role") or "").upper()
    if claimed_role != registration.agent_role.upper():
        raise HTTPException(status_code=403, detail="Agent role mismatch")
    workflow = db.query(AgentWorkflow).filter(
        AgentWorkflow.id == registration.workflow_id,
        AgentWorkflow.tenant_id == actor.tenant_id,
        AgentWorkflow.initiator_id == actor.id,
        AgentWorkflow.thread_id == str(registration.conversation_id),
    ).first()
    if workflow is None or (workflow.dag_plan or {}).get("kind") != GRAPH_WORKFLOW_KIND:
        raise HTTPException(status_code=404, detail="LangGraph workflow not found")
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == actor.tenant_id,
        AIAgent.role_code == claimed_role,
        AIAgent.is_active.is_(True),
    ).first()
    if agent is None:
        raise HTTPException(status_code=403, detail="AI Employee is unavailable")
    _enforce_agent_configuration(db, actor, claims, registration.tool_name)
    approval = ensure_graph_approval(
        db,
        workflow=workflow,
        user=actor,
        agent=agent,
        interrupt_id=registration.interrupt_id,
        value={
            "tool_name": registration.tool_name,
            "action": registration.action,
            "reason": registration.reason,
            "arguments": registration.arguments,
        },
    )
    workflow.status = "AWAITING_APPROVAL"
    db.commit()
    return {
        "approval_id": str(approval.id),
        "workflow_id": str(workflow.id),
        "status": approval.status,
        "interrupt_id": registration.interrupt_id,
    }


@router.post("/{tool_name}/invoke", response_model=ToolInvocationResponse)
def invoke_tool(
    tool_name: str,
    invocation: ToolInvocationRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    actor_and_claims: tuple[User, dict[str, Any]] = Depends(_internal_actor),
) -> ToolInvocationResponse:
    actor, claims = actor_and_claims
    definition = tool_registry.get(tool_name)
    definition.authorize(actor)
    _enforce_agent_configuration(db, actor, claims, tool_name)
    try:
        validated = definition.input_schema.model_validate(invocation.input)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if validated.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    if definition.action != ToolAction.READ_ONLY and validated.audit.idempotency_key:
        lock_key = (
            f"{actor.tenant_id}:{actor.id}:{definition.name}:"
            f"{validated.audit.idempotency_key}"
        )
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )
    existing_result = _idempotent_result(
        db,
        actor,
        definition.name,
        validated.audit.idempotency_key,
    )
    if definition.action != ToolAction.READ_ONLY and existing_result is not None:
        return ToolInvocationResponse(
            tool_name=definition.name,
            action=definition.action,
            correlation_id=str(validated.audit.correlation_id),
            result=existing_result,
        )

    started = time.monotonic()
    audit_input = _audit_payload(validated, definition)
    try:
        result = definition.executor(db, actor, validated)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        add_audit_event(
            db,
            tenant_id=actor.tenant_id,
            actor_user=actor,
            actor_type="AGENT" if claims.get("agent_role") else "USER",
            agent_role=str(claims.get("agent_role") or "SYSTEM").upper(),
            action=definition.audit_action,
            tool_name=definition.name,
            resource_type="TOOL_EXECUTION",
            input_parameters=audit_input,
            output_result={"result_type": type(result).__name__, "result": result},
            request=http_request,
            status="SUCCESS",
            execution_time_ms=elapsed_ms,
        )
        db.commit()
    except HTTPException as exc:
        db.rollback()
        add_audit_event(
            db,
            tenant_id=actor.tenant_id,
            actor_user=actor,
            actor_type="AGENT" if claims.get("agent_role") else "USER",
            agent_role=str(claims.get("agent_role") or "SYSTEM").upper(),
            action=definition.audit_action,
            tool_name=definition.name,
            resource_type="TOOL_EXECUTION",
            input_parameters=audit_input,
            request=http_request,
            status="DENIED" if exc.status_code == 403 else "FAILED",
            error_message=str(exc.detail),
            execution_time_ms=int((time.monotonic() - started) * 1000),
        )
        db.commit()
        raise
    except Exception as exc:
        db.rollback()
        add_audit_event(
            db,
            tenant_id=actor.tenant_id,
            actor_user=actor,
            actor_type="AGENT" if claims.get("agent_role") else "USER",
            agent_role=str(claims.get("agent_role") or "SYSTEM").upper(),
            action=definition.audit_action,
            tool_name=definition.name,
            resource_type="TOOL_EXECUTION",
            input_parameters=audit_input,
            request=http_request,
            status="FAILED",
            error_message=type(exc).__name__,
            execution_time_ms=int((time.monotonic() - started) * 1000),
        )
        db.commit()
        raise HTTPException(status_code=500, detail="Tool execution failed") from exc
    return ToolInvocationResponse(
        tool_name=definition.name,
        action=definition.action,
        correlation_id=str(validated.audit.correlation_id),
        result=result,
    )
