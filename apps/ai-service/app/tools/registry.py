"""LangChain tools built from the backend's tool contracts.

The backend is the single source of every tool's name, description, input schema, action
class, retry policy and terminal flag, and it runs the tool. The AI service used to keep
its own copy of all of that, which drifted; now it asks the backend each turn
(``ToolGatewayClient.list_tools``) and builds a tool that calls straight back through the
gateway. The backend validates every input against the authoritative schema.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.tools.gateway import ToolGatewayClient

_METADATA_KEYS = (
    "action",
    "allowed_roles",
    "allowed_departments",
    "acl_match",
    "timeout_seconds",
    "retry_policy",
    "audit_action",
    "terminal",
)


def tool_from_contract(contract: dict[str, Any], gateway: ToolGatewayClient) -> BaseTool:
    name = str(contract["name"])
    retry = contract.get("retry_policy") or {}
    policy = {
        "timeout_seconds": float(contract.get("timeout_seconds") or 30),
        "max_attempts": int(retry.get("max_attempts") or 1),
        "backoff_seconds": float(retry.get("backoff_seconds") or 0.25),
        "retryable_status_codes": tuple(retry.get("retryable_status_codes") or (429, 502, 503, 504)),
    }

    def invoke_gateway(**kwargs: Any) -> Any:
        return gateway.invoke(name, kwargs, **policy)

    async def ainvoke_gateway(**kwargs: Any) -> Any:
        return await gateway.ainvoke(name, kwargs, **policy)

    metadata = {key: contract.get(key) for key in _METADATA_KEYS}
    metadata["terminal"] = bool(contract.get("terminal"))
    metadata["gateway_only"] = True
    return StructuredTool.from_function(
        func=invoke_gateway,
        coroutine=ainvoke_gateway,
        name=name,
        description=str(contract.get("description") or name),
        args_schema=contract.get("input_schema") or {"type": "object", "properties": {}},
        metadata=metadata,
    )


def build_langchain_tools(gateway: ToolGatewayClient) -> list[BaseTool]:
    return [tool_from_contract(contract, gateway) for contract in gateway.list_tools()]
