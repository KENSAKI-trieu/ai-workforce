"""Pydantic contracts accepted by the internal tool gateway."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class AuditMetadata(BaseModel):
    """Caller-supplied trace data; never used as an authorization source."""

    correlation_id: UUID
    conversation_id: UUID | None = None
    workflow_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class TenantToolInput(BaseModel):
    tenant_id: UUID
    audit: AuditMetadata


class IdempotentToolInput(TenantToolInput):
    @model_validator(mode="after")
    def require_idempotency_key(self) -> "IdempotentToolInput":
        if not self.audit.idempotency_key:
            raise ValueError("audit.idempotency_key is required for mutating tools")
        return self


class RAGSearchInput(TenantToolInput):
    query: str = Field(min_length=2, max_length=5000)
    top_k: int = Field(default=5, ge=1, le=20)
    collections: list[str] | None = Field(default=None, max_length=20)


class WebSearchInput(TenantToolInput):
    """A search phrase sent to Google: never personal or confidential details."""

    query: str = Field(min_length=2, max_length=500)
    max_results: int = Field(default=5, ge=1, le=8)


class EmployeeLookupInput(TenantToolInput):
    employee_id: UUID
    sections: list[Literal["BASIC", "PRIVATE", "CONTRACT", "COMPENSATION", "LEAVE"]] = Field(
        default_factory=lambda: ["BASIC"], min_length=1, max_length=5
    )
    purpose: Literal[
        "SELF_SERVICE",
        "DIRECTORY_LOOKUP",
        "LEAVE_MANAGEMENT",
        "CONTRACT_RENEWAL",
        "PAYROLL_PROCESSING",
        "HR_OPERATIONS",
        "EMPLOYEE_SUPPORT",
        "EXECUTIVE_REVIEW",
    ] = "DIRECTORY_LOOKUP"


class LeaveLookupInput(TenantToolInput):
    employee_id: UUID | None = None
    year: int | None = Field(default=None, ge=2000, le=2100)


class CreateTaskInput(IdempotentToolInput):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    assignee_id: UUID | None = None
    ai_agent_id: UUID | None = None
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"] = "MEDIUM"
    due_date: datetime | None = None


class ExpenseLookupInput(TenantToolInput):
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    breakdown: Literal["SUMMARY", "AGENT", "EMPLOYEE", "DEPARTMENT", "WORKFLOW"] = "SUMMARY"


class GenerateLegalDocumentInput(IdempotentToolInput):
    document_type: str = Field(min_length=2, max_length=100)
    output_format: Literal["docx", "pdf"] = "docx"
    fields: dict[str, Any]

    @field_validator("document_type")
    @classmethod
    def normalize_document_type(cls, value: str) -> str:
        return value.strip().upper()


class SubmitApprovalInput(IdempotentToolInput):
    title: str = Field(min_length=2, max_length=255)
    action_type: Literal[
        "GENERAL_APPROVAL",
        "TASK_APPROVAL",
        "EXPENSE_APPROVAL",
        "CONTENT_APPROVAL",
        "EXTERNAL_ACTION_APPROVAL",
    ]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    payload: dict[str, Any]
    approver_id: UUID | None = None
    expires_at: datetime | None = None

    @field_validator("action_type", mode="before")
    @classmethod
    def normalize_action_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("expires_at")
    @classmethod
    def require_future_expiry(cls, value: datetime | None) -> datetime | None:
        """An already-past deadline creates a gate that is EXPIRED before anyone sees it."""
        if value is None:
            return value
        deadline = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if deadline <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return value


class ContractRiskReviewInput(TenantToolInput):
    """Review contract text the user sent in this conversation.

    Neither the text nor the user's side is an argument. The backend reads both from the
    user's own messages, so what is reviewed is exactly what they sent, from the side they
    said they act for: a model given the side as a parameter filled in NEUTRAL for a user
    who had not answered.
    """

    from_user_message: int = Field(
        default=0,
        ge=0,
        le=5,
        description=(
            "Which of the user's messages holds the contract: 0 is the current message, "
            "1 the one before it, and so on. Use 1 when the current message only answers "
            "which side the user represents."
        ),
    )
    document_scope: Literal["FULL", "EXCERPT"] | None = Field(
        default=None,
        description=(
            "FULL for a whole contract, EXCERPT for a clause or passage. An excerpt is not "
            "faulted for clauses it does not contain. Omit to let the backend decide."
        ),
    )


# --------------------------------------------------------------------------- finance
# Every finance input is a fixed parameter the backend turns into its own query. There is
# no free-form SQL anywhere: the model picks what to look at, never how it is computed.
FinancePeriod = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Accounting period, YYYY-MM.")


class InvoiceLookupInput(TenantToolInput):
    status: Literal["RECEIVED", "MATCHED", "EXCEPTION", "POSTED", "PAID", "REJECTED"] | None = None
    direction: Literal["IN", "OUT"] | None = Field(
        default=None, description="IN for purchase invoices, OUT for sales invoices."
    )
    party: str | None = Field(
        default=None, max_length=255,
        description="Tax code or part of the name of the vendor or customer.",
    )
    number: str | None = Field(default=None, max_length=20, description="Invoice number.")
    period: str | None = FinancePeriod
    limit: int = Field(default=20, ge=1, le=50)


class ProposeJournalEntryInput(IdempotentToolInput):
    invoice_id: UUID = Field(description="The invoice's id, as lookup_invoices returned it.")
    main_account: str | None = Field(
        default=None, pattern=r"^\d{3,10}$",
        description=(
            "Only when the user named the account for the amount before tax (for example "
            "6422). Otherwise omit it: the backend applies the approved rule for this "
            "party or chooses from the company's chart."
        ),
    )


FinanceAccount = Field(pattern=r"^\d{3,10}$", description="Account number of the chart, e.g. 331 or 6422.")


class AccountBalanceInput(TenantToolInput):
    account: str = FinanceAccount
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Accounting period, YYYY-MM.")


class AccountTrendInput(TenantToolInput):
    account: str = FinanceAccount
    from_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="First period, YYYY-MM.")
    to_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Last period, YYYY-MM; at most 24 months after from_period.")


class ExpenseBreakdownInput(TenantToolInput):
    from_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="First period, YYYY-MM.")
    to_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Last period, YYYY-MM; the same as from_period for one month.")
    group_by: Literal["account", "department", "month"] = Field(
        default="account",
        description=(
            "account: by expense account (641, 642 ...). department: by department. month: "
            "month by month over the range, each with its change from the month before."
        ),
    )
    level: Literal[3, 4] = Field(
        default=3, description="With group_by account: 3 for 641, 642 ...; 4 for their sub-accounts (6421, 6428 ...).",
    )
    keyword: str | None = Field(
        default=None, max_length=100,
        description=(
            "Only the lines whose description contains these words, for a kind of cost with "
            "no account of its own (\"quảng cáo\", \"vận chuyển\"). Accents and case are ignored."
        ),
    )


class IncomeStatementInput(TenantToolInput):
    from_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="First period, YYYY-MM.")
    to_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Last period, YYYY-MM; the same as from_period for one month.")


class FinanceDraftsInput(TenantToolInput):
    kind: Literal["JOURNAL", "VOUCHER", "REMINDER"] | None = Field(
        default=None, description="JOURNAL journal entries, VOUCHER payment vouchers, REMINDER payment reminders; omit for all.",
    )
    status: Literal["WAITING", "APPROVED", "REJECTED", "WITHDRAWN"] | None = Field(
        default=None, description="WAITING for a decision, APPROVED, REJECTED or WITHDRAWN; omit for any.",
    )
    scope: Literal["MINE", "TO_DECIDE", "ALL"] = Field(
        default="ALL",
        description="MINE: drafts the user sent. TO_DECIDE: drafts waiting for the user's own decision. ALL: every finance draft.",
    )
    limit: int = Field(default=20, ge=1, le=50)


class SpreadsheetListInput(TenantToolInput):
    pass


class SheetFilter(BaseModel):
    column: str = Field(min_length=1, max_length=255, description="Column name as list_spreadsheets returned it.")
    op: Literal["eq", "contains", "gte", "lte"] = Field(
        default="eq", description="eq equals, contains (text columns), gte at least, lte at most (numbers, dates dd/mm/yyyy).",
    )
    value: str = Field(max_length=255)


class SpreadsheetAnalysisInput(TenantToolInput):
    sheet_id: UUID | None = Field(default=None, description="Omit for the file the user uploaded last.")
    operation: Literal["sum", "count", "average", "min", "max"]
    value_column: str | None = Field(
        default=None, max_length=255,
        description="The number column to compute on, by its name; omit only to count rows.",
    )
    group_by: str | None = Field(default=None, max_length=255, description="Column to group the result by, by its name.")
    period: Literal["day", "month", "quarter", "year"] | None = Field(
        default=None, description="Only when group_by is a date column: group by month, quarter, year or day.",
    )
    filters: list[SheetFilter] = Field(default_factory=list, max_length=5)


class TrialBalanceInput(TenantToolInput):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Accounting period, YYYY-MM.")
    level: Literal[3, 4] = Field(default=3, description="3 for level-1 accounts, 4 for their sub-accounts.")


class LedgerDetailInput(TenantToolInput):
    account: str = FinanceAccount
    date_from: date
    date_to: date
    party: str | None = Field(default=None, max_length=255, description="Tax code or name of a vendor or customer.")
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def ordered(self) -> "LedgerDetailInput":
        if self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        return self


class BudgetVsActualInput(TenantToolInput):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Accounting period, YYYY-MM.")
    department: str | None = Field(
        default=None, max_length=50,
        description="Department code. Omit for every department the user may see.",
    )


class AgingInput(TenantToolInput):
    kind: Literal["RECEIVABLE", "PAYABLE"] = Field(
        description="RECEIVABLE: what customers owe us. PAYABLE: what we owe vendors."
    )
    as_of: date | None = Field(
        default=None,
        description="Report date: the debts open on that day, a past one included; omit for today.",
    )
    min_days_overdue: int | None = Field(default=None, ge=0, le=3650, description="Only debts at least this many days past due.")
    party: str | None = Field(default=None, max_length=255, description="Tax code or name of one vendor or customer.")


class PaymentScheduleInput(TenantToolInput):
    as_of: date | None = Field(default=None, description="Start date; omit for today.")
    horizon_days: int = Field(default=14, ge=1, le=90, description="How many days ahead to look.")


class DraftPaymentVoucherInput(IdempotentToolInput):
    invoice_ids: list[UUID] = Field(
        min_length=1, max_length=50,
        description="Ids of posted purchase invoices of ONE vendor, as payment_schedule or lookup_invoices returned them.",
    )


class DraftPaymentReminderInput(IdempotentToolInput):
    party: str = Field(min_length=2, max_length=255, description="Tax code or name of the customer.")
    level: Literal[1, 2, 3] | None = Field(
        default=None,
        description=(
            "1 friendly notice of amounts due, 2 reminder of overdue amounts, 3 final demand. "
            "Leave it empty unless the user named the level or the tone: the draft then takes "
            "2 when anything is overdue, else 1."
        ),
    )


# --------------------------------------------------------------------------- HR
# The HR tools run the same governed branches as the deterministic HR chat; these are only
# what the model read from the conversation. An email never travels here: the graph masks
# it before the model sees it, so the backend reads it from the user's own messages.
_EMAIL_NOTE = (
    "An email the user typed reaches you masked; leave this empty then, the backend reads "
    "the email from the user's own message."
)


class HRNoArgumentsInput(TenantToolInput):
    """The asker's own record, or a list the asker's scope decides."""


class HRDirectoryInput(TenantToolInput):
    person: str | None = Field(
        default=None,
        max_length=120,
        description="Name of the one person the user asks about, as written. " + _EMAIL_NOTE,
    )
    departments: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Departments the user named, in their own words (\"kế toán\", \"sales\"); "
            "empty for the whole company."
        ),
    )
    managers_only: bool = Field(default=False, description="True to list managers only.")


class HREmployeeProfileInput(TenantToolInput):
    employee: Literal["SELF", "OTHER"] = Field(
        default="SELF",
        description="SELF for the asker's own profile; OTHER for a colleague named by email.",
    )
    purpose: Literal[
        "CONTRACT_RENEWAL",
        "PERFORMANCE_REVIEW",
        "ONBOARDING",
        "PAYROLL_PROCESSING",
    ] | None = Field(
        default=None,
        description=(
            "For OTHER, the business purpose the user stated in this conversation. Never "
            "infer one: leave it empty and the backend asks for it."
        ),
    )


class HRLeaveBalanceInput(TenantToolInput):
    person: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Only when the user asks about somebody else's leave days: that person's name. "
            "Empty for the user's own balance."
        ),
    )


class HRRequestLeaveInput(IdempotentToolInput):
    start_date: date | None = Field(default=None, description="First day off, YYYY-MM-DD.")
    end_date: date | None = Field(default=None, description="Last day off, YYYY-MM-DD.")
    reason: str | None = Field(default=None, max_length=500, description="The reason the user gave.")
    abandon: bool = Field(
        default=False,
        description="True when the user drops a request you were still gathering; nothing is filed.",
    )


class HRCancelLeaveInput(IdempotentToolInput):
    start_date: date | None = Field(
        default=None, description="A day of the request to withdraw, if the user named one."
    )
    end_date: date | None = Field(default=None, description="Its last day, if the user named a range.")


class HRLeaveRequestsInput(TenantToolInput):
    view: Literal["WHO_IS_OFF", "REQUESTS"] = Field(
        description=(
            "WHO_IS_OFF: who is or will be on leave on a day or between two dates. "
            "REQUESTS: leave requests and their status (waiting, approved, rejected)."
        ),
    )
    whose: Literal["SELF", "TEAM"] = Field(
        default="SELF",
        description="For REQUESTS: the user's own requests, or their team's.",
    )
    start_date: date | None = Field(
        default=None,
        description=(
            "First day of the period the user asks about, YYYY-MM-DD, worked out from today's "
            "date: \"tuần này\" starts on this week's Monday, \"tháng 11\" on 1 November. Empty "
            "only when the user names no day or period; WHO_IS_OFF then means today."
        ),
    )
    end_date: date | None = Field(
        default=None,
        description=(
            "Last day of that period (this week's Sunday, 30 November); empty for one day. "
            "A period always needs both dates, or only its first day is answered."
        ),
    )
    departments: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Departments the user named, in their own words; empty for all.",
    )


class HRExportInput(TenantToolInput):
    directory: Literal["employees", "managers"] | None = Field(
        default=None, description="Which list; empty when the user did not say."
    )
    format: Literal["xlsx", "pdf", "json"] | None = Field(
        default=None, description="File format (Excel is xlsx); empty when the user did not say."
    )


class HROnboardingInput(IdempotentToolInput):
    """The new hire is named by email in the user's message, which the backend reads."""


# --------------------------------------------------------------------------- marketing
class MarketingCampaignInput(IdempotentToolInput):
    """Start a campaign from the brief the user wrote in this conversation.

    The brief is not an argument: the backend reads it from the user's own message, so the
    campaign is written from exactly what they asked for, not from a model's summary of it.
    """

    from_user_message: int = Field(
        default=0,
        ge=0,
        le=5,
        description=(
            "Which of the user's messages holds the brief: 0 is the current message, 1 the "
            "one before it, and so on. Use 1 when the current message only says to go ahead."
        ),
    )
