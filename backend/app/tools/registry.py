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
    AccountTrendInput,
    AgingInput,
    BudgetVsActualInput,
    ContractRiskReviewInput,
    CreateTaskInput,
    DraftPaymentReminderInput,
    DraftPaymentVoucherInput,
    EmployeeLookupInput,
    ExpenseBreakdownInput,
    ExpenseLookupInput,
    FinanceDraftsInput,
    GenerateLegalDocumentInput,
    HRCancelLeaveInput,
    HRDirectoryInput,
    HREmployeeProfileInput,
    HRExportInput,
    HRLeaveBalanceInput,
    HRLeaveRequestsInput,
    HRNoArgumentsInput,
    HROnboardingInput,
    HRRequestLeaveInput,
    IncomeStatementInput,
    InvoiceLookupInput,
    LedgerDetailInput,
    PaymentScheduleInput,
    ProposeJournalEntryInput,
    LeaveLookupInput,
    RAGSearchInput,
    SpreadsheetAnalysisInput,
    SpreadsheetListInput,
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

    def permits(self, user: User, db: Session | None = None) -> bool:
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

            allowed = has_permission(db, user, self.permission)
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
    # A write the user's own request authorises -- withdrawing their own leave request,
    # HR opening an onboarding -- governed by the executor's own checks, as in the
    # deterministic chat. The graph runs it without stopping for an approval of the call.
    runs_on_request: bool = False

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
            "runs_on_request": self.runs_on_request,
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
    runs_on_request: bool = False,
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
        runs_on_request=runs_on_request,
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
    for definition in definitions + _finance_definitions() + _hr_definitions():
        registry.register(definition)
    return registry


def _finance_definitions() -> tuple[ToolDefinition, ...]:
    """The Finance agent's tools, each gated by the org-structure box for its work."""
    from app.tools.executors.finance import (
        get_account_balance,
        get_account_trend,
        get_aging,
        get_budget_vs_actual,
        get_expense_breakdown,
        get_income_statement,
        get_ledger_detail,
        get_payment_schedule,
        draft_reminder,
        draft_voucher,
        get_trial_balance,
        analyze_spreadsheet,
        list_finance_drafts,
        list_spreadsheets,
        lookup_invoices,
        propose_journal_entry,
    )

    # Every figure in a reply comes from one of these: fixed parameters, computed in SQL,
    # each result naming its `source`. There is deliberately no free-form query tool.
    reading = "Amounts are exact; quote them, never add or estimate. "
    # Receivables, payables and taxes carry debts and advances at once; netting them hides both.
    two_sided = (
        "Accounts that can sit on either side (131, 331, 333, 338 ...) show their debit and "
        "credit balances separately, never netted: report both. "
    )
    # A drafting tool ends the turn with its own result, so one called on a question
    # both opens an approval nobody asked for and drops the answer the user wanted.
    on_request = (
        "Call it only when the user asks for this draft, in this message or by agreeing "
        "to a draft you offered; a question about invoices, balances, debts or due dates "
        "is answered with the reading tools, never acted on. "
    )

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
            + on_request +
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
            "account (sub-accounts included) for a YYYY-MM period, over every vendor and "
            "customer together: for one party's balance use get_ledger_detail or "
            "ar_ap_aging with that party. " + two_sided + reading,
            AccountBalanceInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.finance.balance", get_account_balance, permission="finance.ledger.view",
        ),
        _definition(
            "get_account_trend",
            "One account month by month over a range of periods (at most 24): opening, "
            "debits, credits and closing balance of each month. Use it for how a balance, "
            "a revenue or a cost moved over time; the reply shows a chart of it. "
            + two_sided + reading,
            AccountTrendInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 30,
            "tool.finance.trend", get_account_trend, permission="finance.ledger.view",
        ),
        _definition(
            "get_expense_breakdown",
            "What the company spent between two periods (expense accounts 6xx and 8xx, "
            "closing entries to 911 left out), by account (level 3 or its sub-accounts at "
            "level 4), by department each with its share of the total, or month by month with "
            "each month's change from the one before. For a kind of cost no account is named "
            "after (advertising, freight, travel), pass its words as keyword: only lines whose "
            "description says so are counted. The reply shows a chart of it. " + reading,
            ExpenseBreakdownInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.expenses", get_expense_breakdown, permission="finance.ledger.view",
        ),
        _definition(
            "get_income_statement",
            "Income statement (báo cáo kết quả kinh doanh) between two periods: revenue, cost "
            "of sales, selling and administrative costs, financial and other income and costs, "
            "profit before and after tax, each line already worked out. Use it for revenue, "
            "profit or loss of a period; the reply shows a chart of it. " + reading,
            IncomeStatementInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.income_statement", get_income_statement, permission="finance.ledger.view",
        ),
        _definition(
            "list_finance_drafts",
            "The finance drafts sent for approval -- journal entries, payment vouchers, payment "
            "reminders -- with their status (waiting, approved, rejected, withdrawn), amount and "
            "who sent them; a voucher also says whether the money was transferred. scope MINE "
            "for what the user sent, TO_DECIDE for what waits for the user's own decision. Use "
            "it for questions about vouchers, reminders or approvals, not invoices.",
            FinanceDraftsInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.finance.drafts", list_finance_drafts, permission="finance.journal.draft",
        ),
        _definition(
            "get_trial_balance",
            "Trial balance (bảng cân đối số phát sinh) of a period: every account's opening, "
            "period and closing balances. " + two_sided + reading,
            TrialBalanceInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.trial_balance", get_trial_balance, permission="finance.ledger.view",
        ),
        _definition(
            "get_ledger_detail",
            "Ledger lines (sổ chi tiết) of one account between two dates, optionally for one "
            "vendor or customer, with the running balance, plus the opening balance, total "
            "debits and credits and closing balance of the whole range (also when the line "
            "list is truncated). " + two_sided + reading,
            LedgerDetailInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.ledger", get_ledger_detail, permission="finance.ledger.view",
        ),
        # No permission on the ACL: the executor decides between every department and the
        # user's own one, which a single box on the ACL cannot express.
        _definition(
            "budget_vs_actual",
            "Budget against actual spending per department and account for a period: what is "
            "left of the budget (remaining), what was overspent (over_amount) and whether it is "
            "over budget. " + reading,
            BudgetVsActualInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.budget", get_budget_vs_actual,
        ),
        _definition(
            "ar_ap_aging",
            "Aging of receivables (customers owe us) or payables (we owe vendors): what is "
            "outstanding per party and how overdue, from posted invoices less payments, on "
            "today or on a past date (as_of). ledger_differences lists parties whose balance "
            "on the ledger (131 or 331) differs from their open invoices -- an advance, or money "
            "not yet matched to an invoice: report it with the party's figures. " + reading,
            AgingInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.aging", get_aging, permission="finance.ar_ap.view",
        ),
        _definition(
            "payment_schedule",
            "Purchase invoices to pay within the next days, earliest due first, with what is "
            "already scheduled, what sits in a payment voucher waiting for approval, and what "
            "still needs a voucher (to_schedule). " + reading,
            PaymentScheduleInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.finance.payment_schedule", get_payment_schedule, permission="finance.ar_ap.view",
        ),
        # A user's own uploaded file, not the books: the executor reads only files the
        # caller uploaded, and the box decides who may analyse at all.
        _definition(
            "list_spreadsheets",
            "The Excel/CSV files the user uploaded to analyse, newest first, with their "
            "columns (name and kind: NUMBER, DATE, TEXT) and row counts. Call it before "
            "analyze_spreadsheet when you do not know the columns.",
            SpreadsheetListInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10,
            "tool.finance.sheets", list_spreadsheets, permission="finance.sheet.analyze",
        ),
        _definition(
            "analyze_spreadsheet",
            "Compute on a file the user uploaded: sum, count, average, minimum or maximum of "
            "one number column, optionally grouped by a column (by month, quarter or year for "
            "a date column) and filtered. Rows labelled as totals in the file are already "
            "left out. Name columns exactly as list_spreadsheets returned them. The figures "
            "come from the user's file, not the company's books: say so. The reply shows a "
            "chart of it. " + reading,
            SpreadsheetAnalysisInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.finance.sheet_analysis", analyze_spreadsheet, permission="finance.sheet.analyze",
        ),
        # Both terminal and approval-opening: nothing is paid or sent on this call.
        _definition(
            "draft_payment_voucher",
            "Draft a payment voucher for posted purchase invoices of one vendor and send it "
            "for approval. It never transfers money. " + on_request +
            "Its result is the answer to the user, and it opens its own approval, so never "
            "submit another one for it.",
            DraftPaymentVoucherInput, ToolAction.WRITE, {"*"}, {"*"}, 30,
            "tool.finance.voucher.draft", draft_voucher,
            terminal=True, opens_approval=True, permission="finance.journal.draft",
        ),
        _definition(
            "draft_payment_reminder",
            "Draft a payment reminder email to one customer from its aging, at level 1 (due), "
            "2 (overdue) or 3 (final), and send it for approval; nothing is emailed before "
            "that. " + on_request +
            "Its result is the answer to the user; never submit another approval for it.",
            DraftPaymentReminderInput, ToolAction.WRITE, {"*"}, {"*"}, 30,
            "tool.finance.reminder.draft", draft_reminder,
            terminal=True, opens_approval=True, permission="finance.reminder.send",
        ),
    )


def _hr_definitions() -> tuple[ToolDefinition, ...]:
    """The HR agent's tools. Each runs the HR chat's own branch for that capability.

    Every one is terminal: the backend writes the answer, so a profile, a salary or a
    colleague's contact never passes through the model. The ACL is open because each
    branch checks the asker itself -- grant, scope, purpose, HR's own role -- as it always
    has in the deterministic chat.
    """
    from app.tools.executors.hr import (
        hr_cancel_leave,
        hr_compensation,
        hr_contract,
        hr_contract_expiry,
        hr_directory,
        hr_employee_profile,
        hr_export,
        hr_leave_balance,
        hr_leave_requests,
        hr_onboarding,
        hr_pending_approvals,
        hr_private_profile,
        hr_request_leave,
    )

    answer = "Its result is the answer to the user; call one HR tool per turn. "
    return (
        _definition(
            "query_company_users_sql",
            "Find a colleague (name, email, department, title, contact) or list the employees "
            "or managers, optionally of some departments, within what the user may see. Use it "
            "for questions like \"X là ai\", \"email của X\", \"danh sách nhân viên phòng ...\". "
            + answer,
            HRDirectoryInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.hr.directory", hr_directory, terminal=True,
        ),
        _definition(
            "get_employee_full_profile",
            "The user's own HR profile (employee=SELF), or a colleague's deeper profile "
            "(employee=OTHER) for a business purpose the user stated, the colleague named by "
            "email. " + answer,
            HREmployeeProfileInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.hr.profile", hr_employee_profile, terminal=True,
        ),
        _definition(
            "get_employee_compensation_summary",
            "The user's own salary and pay. " + answer,
            HRNoArgumentsInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10,
            "tool.hr.compensation", hr_compensation, terminal=True,
        ),
        _definition(
            "get_employee_private_profile",
            "The user's own personal details (address, ID number, bank account, emergency "
            "contact). " + answer,
            HRNoArgumentsInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10,
            "tool.hr.private_profile", hr_private_profile, terminal=True,
        ),
        _definition(
            "get_employee_contract_summary",
            "The user's own labour contracts. " + answer,
            HRNoArgumentsInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10,
            "tool.hr.contract", hr_contract, terminal=True,
        ),
        _definition(
            "query_leave_balance",
            "How many leave days the user has, has used and has left this year. Only the "
            "user's own: if they ask about somebody else, pass that person and the tool "
            "explains. " + answer,
            HRLeaveBalanceInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10,
            "tool.hr.leave_balance", hr_leave_balance, terminal=True,
        ),
        _definition(
            "list_leave_requests",
            "Who is on leave on a day or between two dates (view WHO_IS_OFF), or the status of "
            "leave requests (view REQUESTS), the user's own or their team's. " + answer,
            HRLeaveRequestsInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.hr.leave_requests", hr_leave_requests, terminal=True,
        ),
        # Opens its own approval: the request goes to the user's approver as it is filed.
        _definition(
            "request_leave",
            "File the user's leave request and send it to their approver, only when the user "
            "asks to take leave. Pass only the dates and reason the user gave in this "
            "conversation, never invented ones; call it even when some are missing -- it asks "
            "for them. Convert dates the user wrote (thứ 6 tuần sau, 12/10) to YYYY-MM-DD from "
            "today's date. It opens its own approval, so never submit another one. " + answer,
            HRRequestLeaveInput, ToolAction.WRITE, {"*"}, {"*"}, 20,
            "tool.hr.leave_request", hr_request_leave,
            terminal=True, opens_approval=True,
        ),
        _definition(
            "cancel_leave_request",
            "Withdraw one of the user's own leave requests that is still waiting for "
            "approval, only when the user asks to withdraw or cancel it. Pass its dates if "
            "the user named them; with several waiting it asks which one. " + answer,
            HRCancelLeaveInput, ToolAction.WRITE, {"*"}, {"*"}, 15,
            "tool.hr.leave_cancel", hr_cancel_leave,
            terminal=True, runs_on_request=True,
        ),
        _definition(
            "get_contract_expiry",
            "Labour contracts in force within the user's HR scope and when each ends. " + answer,
            HRNoArgumentsInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 15,
            "tool.hr.contract_expiry", hr_contract_expiry, terminal=True,
        ),
        _definition(
            "list_pending_hr_approvals",
            "HR requests waiting for the user's own approval. " + answer,
            HRNoArgumentsInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 20,
            "tool.hr.pending_approvals", hr_pending_approvals, terminal=True,
        ),
        _definition(
            "export_hr_directory",
            "A download link for the employee or manager list as Excel, PDF or JSON. Leave "
            "a choice empty if the user did not make it; the tool asks. " + answer,
            HRExportInput, ToolAction.READ_ONLY, {"*"}, {"*"}, 10,
            "tool.hr.export", hr_export, terminal=True,
        ),
        _definition(
            "create_onboarding_workflow",
            "Start onboarding for a new hire the user names by email, only when the user asks "
            "for it. HR staff only; the tool checks. " + answer,
            HROnboardingInput, ToolAction.WRITE, {"*"}, {"*"}, 20,
            "tool.hr.onboarding", hr_onboarding,
            terminal=True, runs_on_request=True,
        ),
    )


tool_registry = build_tool_registry()
