"""Authoritative tool metadata, ACL and executor registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.models import AIAgent, User
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
    backoff_seconds: float
    retryable_status_codes: tuple[int, ...] = (429, 502, 503, 504)


@dataclass(frozen=True)
class ToolACL:
    allowed_roles: frozenset[str]
    allowed_departments: frozenset[str]
    match: str = "ROLE_AND_DEPARTMENT"

    def permits(self, user: User) -> bool:
        role_allowed = "*" in self.allowed_roles or user.role in self.allowed_roles
        department_allowed = (
            "*" in self.allowed_departments
            or user.department.upper() in self.allowed_departments
        )
        if self.match == "ROLE_AND_DEPARTMENT":
            return role_allowed and department_allowed
        return role_allowed or department_allowed


@dataclass(frozen=True)
class ToolContext:
    """What an executor is allowed to know about its caller.

    `agent` is the AI Employee row the internal token was minted for, already checked
    against this tool by the gateway. It is None when the token carries no agent identity
    -- a user acting directly -- and executors must then apply no agent-level narrowing.
    Passing the row rather than re-querying it keeps `knowledge_access` and the tool ACL
    reading the same record within one request.
    """

    db: Session
    actor: User
    agent: AIAgent | None = None


ToolExecutor = Callable[[ToolContext, BaseModel], dict[str, Any] | list[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: type[BaseModel]
    action: ToolAction
    acl: ToolACL
    timeout_seconds: float
    retry: RetryPolicy
    audit_action: str
    executor: ToolExecutor
    # A terminal tool's result carries the user-facing `reply`, and the graph ends the
    # turn on it instead of asking the model what to do next.
    terminal: bool = False

    def authorize(self, user: User) -> None:
        if not user.is_active or not self.acl.permits(user):
            raise HTTPException(status_code=403, detail=f"Access denied for tool '{self.name}'")

    def public_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "action": self.action.value,
            "allowed_roles": sorted(self.acl.allowed_roles),
            "allowed_departments": sorted(self.acl.allowed_departments),
            "acl_match": self.acl.match,
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": {
                "max_attempts": self.retry.max_attempts,
                "backoff_seconds": self.retry.backoff_seconds,
                "retryable_status_codes": list(self.retry.retryable_status_codes),
            },
            "audit_action": self.audit_action,
            "terminal": self.terminal,
            "input_schema": self.input_schema.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Duplicate tool: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        definition = self._tools.get(name)
        if definition is None:
            raise HTTPException(status_code=404, detail="Tool not found")
        return definition

    def all(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())


def _definition(
    name: str,
    description: str,
    schema: type[BaseModel],
    action: ToolAction,
    roles: set[str],
    departments: set[str],
    timeout: float,
    audit_action: str,
    executor: ToolExecutor,
    acl_match: str = "ROLE_AND_DEPARTMENT",
    *,
    terminal: bool = False,
) -> ToolDefinition:
    read_only = action == ToolAction.READ_ONLY
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=schema,
        action=action,
        acl=ToolACL(frozenset(roles), frozenset(departments), acl_match),
        timeout_seconds=timeout,
        retry=RetryPolicy(max_attempts=3 if read_only else 1, backoff_seconds=0.25),
        audit_action=audit_action,
        executor=executor,
        terminal=terminal,
    )


def build_tool_registry() -> ToolRegistry:
    from app.tools.executors import (
        create_task,
        generate_legal_document_draft,
        lookup_employee,
        lookup_expenses,
        lookup_leave,
        review_contract_risk,
        search_rag,
        submit_approval_request,
    )

    registry = ToolRegistry()
    definitions = (
        _definition("rag_search", "Search governed tenant knowledge.", RAGSearchInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20, "tool.rag.search", search_rag),
        _definition("employee_lookup", "Read an ACL-filtered employee profile.", EmployeeLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10, "tool.employee.read", lookup_employee),
        _definition("leave_lookup", "Read an ACL-filtered leave balance.", LeaveLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10, "tool.leave.read", lookup_leave),
        _definition("create_task", "Create a tenant task.", CreateTaskInput, ToolAction.WRITE, {"Owner", "Admin", "CEO", "Manager", "Employee"}, {"*"}, 10, "tool.task.create", create_task),
        _definition("expense_lookup", "Read AI spend and usage costs.", ExpenseLookupInput, ToolAction.READ_ONLY, {"Owner", "Admin", "CEO", "Manager"}, {"FINANCE"}, 20, "tool.expense.read", lookup_expenses, "ROLE_OR_DEPARTMENT"),
        _definition("generate_legal_document", "Generate a legal draft for human approval.", GenerateLegalDocumentInput, ToolAction.WRITE, {"Owner", "Admin", "CEO"}, {"LEGAL"}, 45, "tool.legal.generate", generate_legal_document_draft, "ROLE_OR_DEPARTMENT"),
        _definition("submit_approval_request", "Create a human approval gate.", SubmitApprovalInput, ToolAction.EXTERNAL_ACTION, {"Owner", "Admin", "CEO", "Manager", "Employee"}, {"*"}, 10, "tool.approval.submit", submit_approval_request),
        # READ_ONLY in the graph's sense -- it runs without a human approving it first --
        # although it stores the review it produces. That record is idempotent per user,
        # text and side, and the only escalation it can raise is itself an approval for a
        # human to decide, exactly as a review in the deterministic chat does. Gating the
        # review itself would put every analysis behind an approval nobody needs.
        # Terminal: the backend already knows what to tell the user -- the side question,
        # or the review summary behind its card. Handing the result back to the model
        # instead made it re-call the tool and open approvals of its own.
        _definition(
            "audit_contract_risk",
            "Review contract or clause text the user sent in this conversation for legal "
            "risk. The backend reads the text, and the side the user represents, from the "
            "user's own messages; do not paste the contract into the call. Its result is the "
            "answer to the user, so call it at most once per turn.",
            ContractRiskReviewInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 60, "tool.legal.review",
            review_contract_risk, terminal=True,
        ),
    )
    for definition in definitions:
        registry.register(definition)
    return registry


tool_registry = build_tool_registry()
