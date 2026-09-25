"""
API Endpoints for AI Cost Management (Quản lý chi phí AI).
Provides comprehensive token & USD cost metering, breakdown by Agent, Employee, Department, Workflow,
Budget quota management, threshold alerts, and Task-to-Model Routing configuration.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, RoleRequired
from app.models.models import User
from app.schemas.costs import (
    AgentCostResponse,
    BudgetResponse,
    BudgetsAlertsResponse,
    CostSummaryResponse,
    DepartmentCostResponse,
    EmployeeCostResponse,
    RoutingRuleResponse,
    RoutingRuleUpsertRequest,
    BudgetUpsertRequest,
    TokenStatisticsResponse,
    WorkflowCostResponse,
)
from app.domains.platform.audit_service import (
    get_llm_cost_summary,
    get_cost_by_agent,
    get_cost_by_employee,
    get_cost_by_department,
    get_cost_by_workflow,
    get_token_statistics,
    get_budget_limits_and_alerts,
    upsert_budget_limit,
    get_model_routing_rules,
    upsert_model_routing_rule,
    get_month_bounds,
)

router = APIRouter(prefix="/costs", tags=["AI Cost Management"])


def validated_month(
    month: Optional[str] = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="UTC reporting month in YYYY-MM format",
    ),
) -> Optional[str]:
    try:
        get_month_bounds(month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return month


@router.get(
    "/summary",
    response_model=CostSummaryResponse,
    summary="Overall token metrics, cost summary, budget usage & savings",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_cost_summary(
    month: Optional[str] = Depends(validated_month),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> CostSummaryResponse:
    return get_llm_cost_summary(db, current_user.tenant_id, month)


@router.get(
    "/by-agent",
    response_model=list[AgentCostResponse],
    summary="Cost breakdown by AI Agent role",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_cost_by_agent(
    month: Optional[str] = Depends(validated_month),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[AgentCostResponse]:
    return get_cost_by_agent(db, current_user.tenant_id, month)


@router.get(
    "/by-employee",
    response_model=list[EmployeeCostResponse],
    summary="Cost breakdown by Employee / User",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_cost_by_employee(
    month: Optional[str] = Depends(validated_month),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[EmployeeCostResponse]:
    return get_cost_by_employee(db, current_user.tenant_id, month)


@router.get(
    "/by-department",
    response_model=list[DepartmentCostResponse],
    summary="Cost breakdown by Department",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_cost_by_department(
    month: Optional[str] = Depends(validated_month),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[DepartmentCostResponse]:
    return get_cost_by_department(db, current_user.tenant_id, month)


@router.get(
    "/by-workflow",
    response_model=list[WorkflowCostResponse],
    summary="Cost breakdown by Workflow session",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_cost_by_workflow(
    month: Optional[str] = Depends(validated_month),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[WorkflowCostResponse]:
    return get_cost_by_workflow(db, current_user.tenant_id, month)


@router.get(
    "/token-stats",
    response_model=TokenStatisticsResponse,
    summary="Detailed statistics for input/output tokens and cost trends",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_token_statistics(
    month: Optional[str] = Depends(validated_month),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> TokenStatisticsResponse:
    return get_token_statistics(db, current_user.tenant_id, month)


@router.get(
    "/budgets-alerts",
    response_model=BudgetsAlertsResponse,
    summary="Fetch monthly budget quotas, usage status, and threshold alerts",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_budgets_and_alerts(
    month: Optional[str] = Depends(validated_month),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> BudgetsAlertsResponse:
    return get_budget_limits_and_alerts(db, current_user.tenant_id, month)


@router.post(
    "/budgets",
    response_model=BudgetResponse,
    summary="Create or update monthly budget limit & warning threshold",
    dependencies=[Depends(RoleRequired("Admin", "CEO", "Owner"))],
)
def save_budget_limit(
    payload: BudgetUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> BudgetResponse:
    try:
        return upsert_budget_limit(
            db, current_user.tenant_id, payload.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/model-routing",
    response_model=list[RoutingRuleResponse],
    summary="Fetch Task-to-Model Routing configuration rules",
    dependencies=[Depends(RoleRequired("Manager", "Admin", "CEO", "Owner"))],
)
def fetch_model_routing_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[RoutingRuleResponse]:
    return get_model_routing_rules(db, current_user.tenant_id)


@router.post(
    "/model-routing",
    response_model=RoutingRuleResponse,
    summary="Create or update model routing rules per task/agent",
    dependencies=[Depends(RoleRequired("Admin", "CEO", "Owner"))],
)
def save_model_routing_rule(
    payload: RoutingRuleUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> RoutingRuleResponse:
    try:
        return upsert_model_routing_rule(
            db,
            current_user.tenant_id,
            payload.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
