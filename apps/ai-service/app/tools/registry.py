"""Canonical LangChain tool definitions backed by the internal gateway."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from app.tools.gateway import ToolGatewayClient
from app.tools.schemas import (
    ContractRiskReviewInput,
    CreateTaskInput,
    EmployeeLookupInput,
    ExpenseLookupInput,
    GenerateLegalDocumentInput,
    LeaveLookupInput,
    RAGSearchInput,
    SubmitApprovalInput,
)


class ToolAction(StrEnum):
    READ_ONLY = "READ_ONLY"
    WRITE = "WRITE"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: float = 0.25
    retryable_status_codes: tuple[int, ...] = (429, 502, 503, 504)


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: type[BaseModel]
    action: ToolAction
    allowed_roles: frozenset[str]
    allowed_departments: frozenset[str]
    timeout_seconds: float
    retry: RetryPolicy
    audit_action: str
    acl_match: str = "ROLE_AND_DEPARTMENT"
    # A terminal tool's result carries the user-facing `reply`, and the graph ends the
    # turn on it instead of asking the model what to do next.
    terminal: bool = False

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "allowed_roles": sorted(self.allowed_roles),
            "allowed_departments": sorted(self.allowed_departments),
            "acl_match": self.acl_match,
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": {
                "max_attempts": self.retry.max_attempts,
                "backoff_seconds": self.retry.backoff_seconds,
                "retryable_status_codes": list(self.retry.retryable_status_codes),
            },
            "audit_action": self.audit_action,
            "gateway_only": True,
            "terminal": self.terminal,
        }

    def as_langchain_tool(self, gateway: ToolGatewayClient) -> BaseTool:
        def invoke_gateway(**kwargs: Any) -> Any:
            payload = self.input_schema.model_validate(kwargs).model_dump(mode="json")
            return gateway.invoke(
                self.name,
                payload,
                timeout_seconds=self.timeout_seconds,
                max_attempts=self.retry.max_attempts,
                backoff_seconds=self.retry.backoff_seconds,
                retryable_status_codes=self.retry.retryable_status_codes,
            )

        async def ainvoke_gateway(**kwargs: Any) -> Any:
            payload = self.input_schema.model_validate(kwargs).model_dump(mode="json")
            return await gateway.ainvoke(
                self.name,
                payload,
                timeout_seconds=self.timeout_seconds,
                max_attempts=self.retry.max_attempts,
                backoff_seconds=self.retry.backoff_seconds,
                retryable_status_codes=self.retry.retryable_status_codes,
            )

        return StructuredTool.from_function(
            func=invoke_gateway,
            coroutine=ainvoke_gateway,
            name=self.name,
            description=self.description,
            args_schema=self.input_schema,
            metadata=self.metadata,
        )


class ToolRegistry:
    def __init__(self, tools: tuple[ToolDescriptor, ...] = ()) -> None:
        self._tools: dict[str, ToolDescriptor] = {}
        for descriptor in tools:
            self.register(descriptor)

    def register(self, tool: ToolDescriptor) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDescriptor:
        return self._tools[name]

    def all(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._tools.values())

    def langchain_tools(self, gateway: ToolGatewayClient) -> list[BaseTool]:
        return [descriptor.as_langchain_tool(gateway) for descriptor in self.all()]


def _tool(
    name: str,
    description: str,
    schema: type[BaseModel],
    action: ToolAction,
    roles: set[str],
    departments: set[str],
    timeout: float,
    audit_action: str,
    acl_match: str = "ROLE_AND_DEPARTMENT",
    *,
    terminal: bool = False,
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=description,
        input_schema=schema,
        action=action,
        allowed_roles=frozenset(roles),
        allowed_departments=frozenset(departments),
        timeout_seconds=timeout,
        retry=RetryPolicy(max_attempts=3 if action == ToolAction.READ_ONLY else 1),
        audit_action=audit_action,
        acl_match=acl_match,
        terminal=terminal,
    )


tool_registry = ToolRegistry((
    _tool("rag_search", "Search governed tenant knowledge through the backend.", RAGSearchInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20, "tool.rag.search"),
    _tool("employee_lookup", "Read an ACL-filtered employee profile through the backend.", EmployeeLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10, "tool.employee.read"),
    _tool("leave_lookup", "Read an ACL-filtered leave balance through the backend.", LeaveLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10, "tool.leave.read"),
    _tool("create_task", "Create a tenant task through the backend.", CreateTaskInput, ToolAction.WRITE, {"Owner", "Admin", "CEO", "Manager", "Employee"}, {"*"}, 10, "tool.task.create"),
    _tool("expense_lookup", "Read AI spend and usage costs through the backend.", ExpenseLookupInput, ToolAction.READ_ONLY, {"Owner", "Admin", "CEO", "Manager"}, {"FINANCE"}, 20, "tool.expense.read", "ROLE_OR_DEPARTMENT"),
    _tool("generate_legal_document", "Generate a legal draft for human approval through the backend.", GenerateLegalDocumentInput, ToolAction.WRITE, {"Owner", "Admin", "CEO"}, {"LEGAL"}, 45, "tool.legal.generate", "ROLE_OR_DEPARTMENT"),
    _tool("submit_approval_request", "Create a human approval gate through the backend.", SubmitApprovalInput, ToolAction.EXTERNAL_ACTION, {"Owner", "Admin", "CEO", "Manager", "Employee"}, {"*"}, 10, "tool.approval.submit"),
    # READ_ONLY so a review runs without a human approving it first: it analyses text the
    # user already sent, and the one escalation it can raise is itself an approval.
    _tool(
        "audit_contract_risk",
        "Review contract or clause text the user sent in this conversation for legal "
        "risk. The backend reads the text, and the side the user represents, from the "
        "user's own messages; do not paste the contract into the call. Its result is the "
        "answer to the user, so call it at most once per turn.",
        ContractRiskReviewInput,
        ToolAction.READ_ONLY,
        {"*"},
        {"*"},
        60,
        "tool.legal.review",
        # The backend already knows what to tell the user -- the side question, or the
        # review summary behind its card. Handing the result back to the model instead
        # made it re-call the tool and open approvals of its own.
        terminal=True,
    ),
))


def build_langchain_tools(gateway: ToolGatewayClient) -> list[BaseTool]:
    return tool_registry.langchain_tools(gateway)
