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
    AccountBalanceInput,
    AgingInput,
    BudgetVsActualInput,
    ContractRiskReviewInput,
    CreateTaskInput,
    EmployeeLookupInput,
    ExpenseLookupInput,
    GenerateLegalDocumentInput,
    InvoiceLookupInput,
    LedgerDetailInput,
    PaymentScheduleInput,
    ProposeJournalEntryInput,
    LeaveLookupInput,
    RAGSearchInput,
    SubmitApprovalInput,
    TrialBalanceInput,
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
    # The position permission the user must hold, ticked in org-structure. Checked on top
    # of the role/department match, which tools gated by a permission leave open ("*").
    permission: str | None = None

    def permits(self, user: User) -> bool:
        role_allowed = "*" in self.allowed_roles or user.role in self.allowed_roles
        department_allowed = (
            "*" in self.allowed_departments
            or user.department.upper() in self.allowed_departments
        )
        if self.match == "ROLE_AND_DEPARTMENT":
            allowed = role_allowed and department_allowed
        else:
            allowed = role_allowed or department_allowed
        if allowed and self.permission:
            from app.domains.platform.position_service import has_permission

            allowed = has_permission(None, user, self.permission)
        return allowed


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
    # The tool's effect is to put something in front of a human approver -- a draft waiting
    # for sign-off -- so the graph runs it without stopping for an approval of the call
    # first. Stopping as well meant approving the same thing twice.
    opens_approval: bool = False

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
            "required_permission": self.acl.permission,
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": {
                "max_attempts": self.retry.max_attempts,
                "backoff_seconds": self.retry.backoff_seconds,
                "retryable_status_codes": list(self.retry.retryable_status_codes),
            },
            "audit_action": self.audit_action,
            "terminal": self.terminal,
            "opens_approval": self.opens_approval,
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
    opens_approval: bool = False,
    permission: str | None = None,
) -> ToolDefinition:
    read_only = action == ToolAction.READ_ONLY
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=schema,
        action=action,
        acl=ToolACL(frozenset(roles), frozenset(departments), acl_match, permission),
        timeout_seconds=timeout,
        retry=RetryPolicy(max_attempts=3 if read_only else 1, backoff_seconds=0.25),
        audit_action=audit_action,
        executor=executor,
        terminal=terminal,
        opens_approval=opens_approval,
    )


def build_tool_registry() -> ToolRegistry:
    from app.tools.executors.approvals import submit_approval_request
    from app.tools.executors.finance import lookup_expenses
    from app.tools.executors.hr import lookup_employee, lookup_leave
    from app.tools.executors.knowledge import search_rag
    from app.tools.executors.legal import generate_legal_document_draft, review_contract_risk
    from app.tools.executors.tasks import create_task
    from app.domains.legal.legal_documents import document_catalogue

    registry = ToolRegistry()
    definitions = (
        _definition("rag_search", "Search governed tenant knowledge.", RAGSearchInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20, "tool.rag.search", search_rag),
        _definition("employee_lookup", "Read an ACL-filtered employee profile.", EmployeeLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10, "tool.employee.read", lookup_employee),
        _definition("leave_lookup", "Read an ACL-filtered leave balance.", LeaveLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10, "tool.leave.read", lookup_leave),
        _definition("create_task", "Create a tenant task.", CreateTaskInput, ToolAction.WRITE, {"Owner", "Admin", "CEO", "Manager", "Employee"}, {"*"}, 10, "tool.task.create", create_task),
        # Gated by the org-structure permission alone: the FINANCE-department exception it
        # used to carry could not be switched off from the position a person holds.
        _definition("expense_lookup", "Read AI spend and usage costs.", ExpenseLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20, "tool.expense.read", lookup_expenses, permission="finance.expense.view"),
        # Terminal, and it opens its own approval: the draft waits for Owner/Admin/CEO
        # sign-off before its requester can download it, so nothing leaves on this call.
        # It checks the fields before storing anything and says which are missing.
        _definition(
            "generate_legal_document",
            "Draft a legal document from one of the company's templates and send it for "
            "approval, only when the user asks for a document to be drafted. Put in "
            "`fields` only values the user actually gave in this conversation; never "
            "invent names, dates, amounts or terms. Call it even when some are missing: "
            "it tells the user which ones it still needs. Its result is the answer to the "
            "user, and the draft goes to approval by itself, so never submit another "
            "approval for it. Templates, as document_type (label): field names, * = "
            "required, with options and defaults:\n" + document_catalogue(),
            GenerateLegalDocumentInput, ToolAction.WRITE, {"*"}, {"*"}, 45,
            "tool.legal.generate", generate_legal_document_draft,
            terminal=True, opens_approval=True, permission="legal.document.generate",
        ),
        _definition("submit_approval_request", "Create a human approval gate.", SubmitApprovalInput, ToolAction.EXTERNAL_ACTION, {"Owner", "Admin", "CEO", "Manager", "Employee"}, {"*"}, 10, "tool.approval.submit", submit_approval_request),
        # READ_ONLY in the graph's sense -- it runs without a human approving it first --
        # although it stores the review it produces. That record is idempotent per user,
        # text and side, and it sends nothing for approval: the reviewer does that from the
        # saved review, as after a review in the deterministic chat. Gating the review
        # itself would put every analysis behind an approval nobody needs.
        # Terminal: the backend already knows what to tell the user -- the side question,
        # or the review summary behind its card. Handing the result back to the model
        # instead made it re-call the tool and open approvals of its own.
        # 90s: a contract not in Vietnamese is translated first, in parallel blocks of up to
        # 50s each, and the backend waits at most 120s for the whole graph turn.
        _definition(
            "audit_contract_risk",
            "Review contract or clause text the user sent in this conversation for legal "
            "risk. The backend reads the text, and the side the user represents, from the "
            "user's own messages; do not paste the contract into the call. Its result is the "
            "answer to the user, so call it at most once per turn.",
            ContractRiskReviewInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 90, "tool.legal.review",
            review_contract_risk, terminal=True,
        ),
    )
    for definition in definitions + _finance_definitions():
        registry.register(definition)
    return registry


def _finance_definitions() -> tuple[ToolDefinition, ...]:
    """The Finance agent's tools, each gated by the org-structure box for its work."""
    from app.tools.executors.finance import (
        get_account_balance,
        get_aging,
        get_budget_vs_actual,
        get_ledger_detail,
        get_payment_schedule,
        get_trial_balance,
        lookup_invoices,
        propose_journal_entry,
    )

    # Every figure in a reply comes from one of these: fixed parameters, computed in SQL,
    # each result naming its `source`. There is deliberately no free-form query tool.
    reading = "Amounts are exact; quote them, never add or estimate. "

    return (
        _definition(
            "lookup_invoices",
            "List the company's purchase (IN) or sales (OUT) invoices with their status, "
            "matching exceptions and amounts. Use it to answer which invoices exist, which "
            "are waiting or flagged, or to find the invoice the user means.",
            InvoiceLookupInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.finance.invoices", lookup_invoices,
            permission="finance.invoice.process",
        ),
        # Terminal and approval-opening, like generate_legal_document: the draft waits for
        # whoever its amount requires, so the graph does not stop for a second approval.
        _definition(
            "propose_journal_entry",
            "Draft the journal entry for one matched invoice and send it for approval. "
            "The amounts come from the invoice; pass main_account only if the user named "
            "the account. Its result is the answer to the user, and it opens its own "
            "approval, so never submit another one for it. Call it at most once per invoice.",
            ProposeJournalEntryInput, ToolAction.WRITE, {"*"}, {"*"}, 45,
            "tool.finance.journal.propose", propose_journal_entry,
            terminal=True, opens_approval=True, permission="finance.journal.draft",
        ),
        _definition(
            "get_account_balance",
            "Opening balance, debits and credits in the period, and closing balance of one "
            "account (sub-accounts included) for a YYYY-MM period. " + reading,
            AccountBalanceInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.finance.balance", get_account_balance, permission="finance.ledger.view",
        ),
        _definition(
            "get_trial_balance",
            "Trial balance (bảng cân đối số phát sinh) of a period: every account's opening, "
            "period and closing balances. " + reading,
            TrialBalanceInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.trial_balance", get_trial_balance, permission="finance.ledger.view",
        ),
        _definition(
            "get_ledger_detail",
            "Ledger lines (sổ chi tiết) of one account between two dates, optionally for one "
            "vendor or customer, with the running balance. " + reading,
            LedgerDetailInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.ledger", get_ledger_detail, permission="finance.ledger.view",
        ),
        # No permission on the ACL: the executor decides between every department and the
        # user's own one, which a single box on the ACL cannot express.
        _definition(
            "budget_vs_actual",
            "Budget against actual spending per department and account for a period, with "
            "the variance and whether it is over budget. " + reading,
            BudgetVsActualInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.budget", get_budget_vs_actual,
        ),
        _definition(
            "ar_ap_aging",
            "Aging of receivables (customers owe us) or payables (we owe vendors): what is "
            "outstanding per party and how overdue, from posted invoices less payments. " + reading,
            AgingInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.aging", get_aging, permission="finance.ar_ap.view",
        ),
        _definition(
            "payment_schedule",
            "Purchase invoices to pay within the next days, earliest due first, with what is "
            "already scheduled. " + reading,
            PaymentScheduleInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.finance.payment_schedule", get_payment_schedule, permission="finance.ar_ap.view",
        ),
    )


tool_registry = build_tool_registry()
