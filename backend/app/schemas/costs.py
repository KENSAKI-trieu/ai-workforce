"""Validated request and response contracts for AI cost management."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


ScopeType = Literal["TENANT", "DEPARTMENT", "AGENT", "USER"]
AgentRole = Literal["CEO", "HR", "LEGAL", "FINANCE", "KNOWLEDGE", "MARKETING", "ALL"]
RoutingStrategy = Literal["LOW_COST", "BALANCED", "HIGH_PERFORMANCE"]


class CostSummaryResponse(BaseModel):
    period_start: str
    period_end: str
    total_requests: int
    total_prompt_tokens: int
    total_cached_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_estimated_cost_usd: float
    monthly_budget_usd: float
    budget_usage_pct: float
    estimated_savings_usd: float
    savings_baseline_model: str
    active_models_count: int
    legacy_records_excluded: int


class AgentCostResponse(BaseModel):
    agent_role: str
    requests: int
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost_usd: float
    models_used: list[str]


class EmployeeCostResponse(BaseModel):
    user_id: str
    full_name: str
    email: Optional[str]
    department: str
    requests: int
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost_usd: float


class DepartmentCostResponse(BaseModel):
    department: str
    requests: int
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost_usd: float


class WorkflowCostResponse(BaseModel):
    workflow_id: str
    title: str
    status: str
    last_active: Optional[str]
    requests: int
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost_usd: float


class DailyCostResponse(BaseModel):
    date: str
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ModelCostResponse(BaseModel):
    model_name: str
    requests: int
    prompt_tokens: int
    cached_prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class TokenStatisticsResponse(BaseModel):
    daily_trends: list[DailyCostResponse]
    model_distribution: list[ModelCostResponse]


class BudgetUpsertRequest(BaseModel):
    scope_type: ScopeType
    scope_id: str = Field(min_length=1, max_length=100)
    monthly_budget_usd: float = Field(gt=0)
    alert_threshold_pct: int = Field(ge=1, le=100)
    is_active: bool = True

    @field_validator("scope_type", mode="before")
    @classmethod
    def normalize_scope_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("scope_id")
    @classmethod
    def strip_scope_id(cls, value: str) -> str:
        return value.strip()


class BudgetResponse(BudgetUpsertRequest):
    id: str


class BudgetUsageResponse(BudgetResponse):
    current_spend_usd: float
    usage_pct: float
    status: Literal["NORMAL", "WARNING", "EXCEEDED", "INACTIVE"]


class CostAlertResponse(BaseModel):
    id: str
    severity: Literal["MEDIUM", "HIGH"]
    title: str
    message: str
    timestamp: str


class BudgetsAlertsResponse(BaseModel):
    period_start: str
    period_end: str
    budgets: list[BudgetUsageResponse]
    alerts: list[CostAlertResponse]
    total_alerts_count: int


class RoutingRuleUpsertRequest(BaseModel):
    id: Optional[str] = None
    task_type: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_]+$")
    agent_role: AgentRole
    preferred_model: str = Field(min_length=1, max_length=100)
    fallback_model: str = Field(min_length=1, max_length=100)
    max_tokens: int = Field(gt=0, le=1_000_000)
    cost_saving_strategy: RoutingStrategy
    is_active: bool = True

    @field_validator("task_type", "agent_role", "cost_saving_strategy", mode="before")
    @classmethod
    def normalize_uppercase(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("preferred_model", "fallback_model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip().lower()


class RoutingRuleResponse(RoutingRuleUpsertRequest):
    id: str
