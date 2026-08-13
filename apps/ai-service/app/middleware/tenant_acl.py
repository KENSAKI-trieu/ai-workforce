"""Inject tenant context and enforce the model-visible tool subset."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import SystemMessage, ToolMessage

from app.middleware.context import AgentRuntimeContext


def _context(request: ModelRequest | ToolCallRequest) -> AgentRuntimeContext:
    runtime = request.runtime
    context = getattr(runtime, "context", None)
    if not isinstance(context, AgentRuntimeContext):
        raise PermissionError("Trusted agent runtime context is required")
    return context


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or tool.get("function", {}).get("name") or "")
    return str(getattr(tool, "name", ""))


def _metadata_allows(tool: Any, context: AgentRuntimeContext) -> bool:
    metadata = getattr(tool, "metadata", None) or {}
    roles = set(metadata.get("allowed_roles") or {"*"})
    departments = set(metadata.get("allowed_departments") or {"*"})
    role_allowed = "*" in roles or context.role in roles
    department_allowed = "*" in departments or context.department.upper() in departments
    if metadata.get("acl_match", "ROLE_AND_DEPARTMENT") == "ROLE_OR_DEPARTMENT":
        return role_allowed or department_allowed
    return role_allowed and department_allowed


def _system_message(request: ModelRequest, context: AgentRuntimeContext) -> SystemMessage:
    security_context = (
        "Authoritative execution context (do not modify or infer permissions): "
        f"tenant={context.tenant_id}; role={context.role}; "
        f"department={context.department}; agent_role={context.agent_role}. "
        "Only the tools exposed on this request may be used."
    )
    blocks = [] if request.system_message is None else list(request.system_message.content_blocks)
    blocks.append({"type": "text", "text": security_context})
    return SystemMessage(content=blocks)


def _model_request(request: ModelRequest) -> ModelRequest:
    context = _context(request)
    tools = [
        tool
        for tool in request.tools
        if context.is_tool_allowed(_tool_name(tool)) and _metadata_allows(tool, context)
    ]
    return request.override(
        tools=tools,
        system_message=_system_message(request, context),
    )


def _tool_request(request: ToolCallRequest) -> ToolCallRequest:
    context = _context(request)
    name = str(request.tool_call.get("name") or "")
    if not context.is_tool_allowed(name) or not _metadata_allows(request.tool, context):
        raise PermissionError(f"Tool is not allowed in this runtime context: {name}")

    arguments = dict(request.tool_call.get("args") or {})
    arguments["tenant_id"] = str(context.tenant_id)
    audit = {
        "correlation_id": str(context.correlation_id),
        "conversation_id": str(context.conversation_id) if context.conversation_id else None,
        "workflow_id": str(context.workflow_id) if context.workflow_id else None,
    }
    action = str((getattr(request.tool, "metadata", None) or {}).get("action", "READ_ONLY"))
    if action != "READ_ONLY":
        call_id = str(request.tool_call.get("id") or uuid.uuid4())
        audit["idempotency_key"] = f"agent:{context.correlation_id}:{call_id}"[:200]
    arguments["audit"] = {key: value for key, value in audit.items() if value is not None}
    return request.override(tool_call={**request.tool_call, "args": arguments})


class TenantACLContextMiddleware(AgentMiddleware):
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(_model_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(_model_request(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        return handler(_tool_request(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        return await handler(_tool_request(request))
