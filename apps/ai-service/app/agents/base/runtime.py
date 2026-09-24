"""Build what one governed graph turn runs with: identity, tools and the decision model."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.llm.factory import configured_chat_models
from app.governance.guardrails import is_tool_allowed
from app.governance.middleware.context import AgentRuntimeContext
from app.governance.middleware.observability import GatewayTelemetrySink
from app.agents.base.decision import DeterministicDecisionProvider, LangChainDecisionProvider
from app.agents.base.nodes import OrchestrationRuntimeContext
from app.schemas.orchestration import OrchestrationRequest
from app.tools.gateway import ToolGatewayClient, ToolGatewayError
from app.tools.registry import build_langchain_tools

# Fields the orchestrator injects from the trusted runtime context. The system prompt
# already tells the model not to produce them; leaving them in the advertised schema also
# marked them required, so a compliant answer looked incomplete against its own contract.
SERVER_INJECTED_TOOL_FIELDS = ("tenant_id", "audit")


class ToolContractsUnavailable(RuntimeError):
    """The backend's tool contracts could not be read, so the turn cannot be governed."""


def model_facing_schema(tool: Any) -> dict[str, Any]:
    if tool.args_schema is None:
        return {}
    raw = tool.args_schema
    schema = dict(raw if isinstance(raw, dict) else raw.model_json_schema())
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


def build_runtime_context(
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
    tenant_instructions: str = "",
) -> OrchestrationRuntimeContext:
    gateway = ToolGatewayClient(settings.BACKEND_TOOL_GATEWAY_URL, tool_jwt)
    try:
        contract_tools = build_langchain_tools(gateway)
    except ToolGatewayError as exc:
        # Refused rather than run tool-less: a graph that cannot see its tools would
        # answer as if the tenant had no knowledge base. The backend falls back to its
        # deterministic flow on a 5xx from here.
        raise ToolContractsUnavailable(str(exc)) from exc
    tools = {
        tool.name: tool
        for tool in contract_tools
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
                "input_schema": model_facing_schema(tool),
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
            tenant_instructions=tenant_instructions,
        )
    else:
        decision_provider = DeterministicDecisionProvider()
    return OrchestrationRuntimeContext(
        security=security,
        decision_provider=decision_provider,
        tools=tools,
        approval_registrar=gateway.create_graph_approval,
    )


def initial_state(request: OrchestrationRequest) -> dict:
    return {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "role": request.role,
        "department": request.department,
        "conversation_id": request.conversation_id,
        "workflow_id": request.workflow_id,
        # Earlier turns first, so the model reads the current message in its context; the
        # nodes still act on the latest user message (retrieval, input guard).
        "messages": [
            *(item.model_dump() for item in request.history),
            {"role": "user", "content": request.message},
        ],
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
