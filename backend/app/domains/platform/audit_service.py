"""Audit logging and provider-backed LLM cost metering."""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Query, Session

from app.models.models import (
    AgentWorkflow,
    AuditLog,
    CostBudget,
    LLMCostLog,
    ModelRoutingRule,
    User,
)
from app.domains.platform.cost_calculator import (
    PRICING_VERSION,
    calculate_llm_cost,
    normalize_model_name,
    supported_model_names,
)

logger = logging.getLogger(__name__)

METERED_USAGE_SOURCES = ("PROVIDER", "MANUAL_IMPORT")
VALID_BUDGET_SCOPES = {"TENANT", "DEPARTMENT", "AGENT", "USER"}
VALID_DEPARTMENTS = {"BOARD", "HR", "LEGAL", "IT", "FINANCE", "SALES", "ALL"}
VALID_AGENT_ROLES = {"CEO", "HR", "LEGAL", "IT", "FINANCE", "SALES", "KNOWLEDGE"}
VALID_ROUTING_STRATEGIES = {"LOW_COST", "BALANCED", "HIGH_PERFORMANCE"}
BASELINE_MODEL = "gpt-4o"


def _money(value: Decimal | float | int, places: int = 6) -> float:
    return round(float(value), places)


def get_month_bounds(
    month: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """Return an inclusive UTC month start and exclusive next-month boundary."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if month:
        try:
            year_text, month_text = month.split("-", maxsplit=1)
            year, month_number = int(year_text), int(month_text)
            start = datetime(year, month_number, 1, tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ValueError("month must use YYYY-MM format") from exc
    else:
        start = current.astimezone(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

    if start.month == 12:
        end = datetime(start.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(start.year, start.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _metered_logs_query(
    db: Session,
    tenant_id: uuid.UUID,
    month: Optional[str] = None,
) -> tuple[Query, datetime, datetime]:
    start, end = get_month_bounds(month)
    query = db.query(LLMCostLog).filter(
        LLMCostLog.tenant_id == tenant_id,
        LLMCostLog.created_at >= start,
        LLMCostLog.created_at < end,
        LLMCostLog.usage_source.in_(METERED_USAGE_SOURCES),
    )
    return query, start, end


def _resolve_department(
    db: Session,
    tenant_id: uuid.UUID,
    agent_role: str,
    user_id: Optional[uuid.UUID],
    department: Optional[str],
) -> str:
    if department:
        normalized = department.strip().upper()
        if normalized not in VALID_DEPARTMENTS:
            raise ValueError(f"Unsupported department '{department}'")
        return normalized
    if user_id:
        user = db.query(User).filter(
            User.id == user_id, User.tenant_id == tenant_id
        ).first()
        if not user:
            raise ValueError("user_id does not belong to tenant")
        return user.department
    if agent_role == "CEO":
        return "BOARD"
    return agent_role if agent_role in VALID_DEPARTMENTS else "ALL"


def log_audit_action(
    db: Session,
    tenant_id: uuid.UUID,
    agent_role: str,
    tool_name: str,
    input_parameters: Optional[dict[str, Any]] = None,
    output_result: Optional[dict[str, Any]] = None,
    workflow_id: Optional[uuid.UUID] = None,
    execution_time_ms: int = 150,
) -> AuditLog:
    """Log an agent tool execution action."""
    log = AuditLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        agent_role=agent_role,
        tool_name=tool_name,
        input_parameters=input_parameters,
        output_result=output_result,
        execution_time_ms=execution_time_ms,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def log_llm_cost(
    db: Session,
    tenant_id: uuid.UUID,
    agent_role: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    workflow_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    department: Optional[str] = None,
    cached_prompt_tokens: int = 0,
    usage_source: str = "PROVIDER",
) -> LLMCostLog:
    """Persist usage returned by an LLM provider and its deterministic cost.

    Callers must pass token counters from the provider response. This function
    intentionally has no token defaults and rejects unknown models instead of
    silently applying a guessed fallback price.
    """
    source = usage_source.strip().upper()
    if source not in METERED_USAGE_SOURCES:
        raise ValueError(
            f"usage_source must be one of {', '.join(METERED_USAGE_SOURCES)}"
        )
    role = agent_role.strip().upper()
    if role not in VALID_AGENT_ROLES:
        raise ValueError(f"Unsupported agent_role '{agent_role}'")

    # Resolve now so an unsupported snapshot fails before anything is written.
    normalize_model_name(model_name)
    total_cost = calculate_llm_cost(
        model_name,
        prompt_tokens,
        completion_tokens,
        cached_prompt_tokens,
    )
    resolved_department = _resolve_department(
        db, tenant_id, role, user_id, department
    )

    cost_log = LLMCostLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        user_id=user_id,
        department=resolved_department,
        agent_role=role,
        model_name=model_name.strip(),
        prompt_tokens=prompt_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=total_cost,
        usage_source=source,
        pricing_version=PRICING_VERSION,
    )
    db.add(cost_log)
    db.commit()
    db.refresh(cost_log)
    return cost_log


def get_audit_logs(
    db: Session, tenant_id: uuid.UUID, limit: int = 50
) -> list[dict[str, Any]]:
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(log.id),
            "agent_role": log.agent_role,
            "tool_name": log.tool_name,
            "input_parameters": log.input_parameters,
            "execution_time_ms": log.execution_time_ms,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


def _calculate_savings(logs: list[LLMCostLog]) -> Decimal:
    savings = Decimal("0")
    for log in logs:
        baseline = calculate_llm_cost(
            BASELINE_MODEL,
            log.prompt_tokens,
            log.completion_tokens,
            log.cached_prompt_tokens,
        )
        actual = Decimal(str(log.estimated_cost_usd))
        if baseline > actual:
            savings += baseline - actual
    return savings


def get_llm_cost_summary(
    db: Session,
    tenant_id: uuid.UUID,
    month: Optional[str] = None,
) -> dict[str, Any]:
    query, start, end = _metered_logs_query(db, tenant_id, month)
    logs = query.all()
    total_prompt = sum(log.prompt_tokens for log in logs)
    total_cached = sum(log.cached_prompt_tokens for log in logs)
    total_completion = sum(log.completion_tokens for log in logs)
    total_cost = sum(
        (Decimal(str(log.estimated_cost_usd)) for log in logs), Decimal("0")
    )

    tenant_budget = db.query(CostBudget).filter(
        CostBudget.tenant_id == tenant_id,
        CostBudget.scope_type == "TENANT",
        CostBudget.scope_id == "ALL",
        CostBudget.is_active.is_(True),
    ).first()
    monthly_budget = (
        Decimal(str(tenant_budget.monthly_budget_usd))
        if tenant_budget
        else Decimal("0")
    )
    usage_pct = (
        (total_cost / monthly_budget * Decimal("100"))
        if monthly_budget > 0
        else Decimal("0")
    )
    legacy_count = db.query(LLMCostLog).filter(
        LLMCostLog.tenant_id == tenant_id,
        LLMCostLog.created_at >= start,
        LLMCostLog.created_at < end,
        LLMCostLog.usage_source == "LEGACY_ESTIMATE",
    ).count()

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "total_requests": len(logs),
        "total_prompt_tokens": total_prompt,
        "total_cached_prompt_tokens": total_cached,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "total_estimated_cost_usd": _money(total_cost),
        "monthly_budget_usd": _money(monthly_budget, 2),
        "budget_usage_pct": _money(usage_pct, 1),
        "estimated_savings_usd": _money(_calculate_savings(logs)),
        "savings_baseline_model": BASELINE_MODEL,
        "active_models_count": len({log.model_name for log in logs}),
        "legacy_records_excluded": legacy_count,
    }


def _base_group() -> dict[str, Any]:
    return {
        "requests": 0,
        "prompt_tokens": 0,
        "cached_prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "total_cost_usd": Decimal("0"),
    }


def _add_log(group: dict[str, Any], log: LLMCostLog) -> None:
    group["requests"] += 1
    group["prompt_tokens"] += log.prompt_tokens
    group["cached_prompt_tokens"] += log.cached_prompt_tokens
    group["completion_tokens"] += log.completion_tokens
    group["total_tokens"] += log.prompt_tokens + log.completion_tokens
    group["total_cost_usd"] += Decimal(str(log.estimated_cost_usd))


def _finalize_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for group in groups:
        group["total_cost_usd"] = _money(group["total_cost_usd"])
    groups.sort(key=lambda item: item["total_cost_usd"], reverse=True)
    return groups


def get_cost_by_agent(
    db: Session, tenant_id: uuid.UUID, month: Optional[str] = None
) -> list[dict[str, Any]]:
    logs = _metered_logs_query(db, tenant_id, month)[0].all()
    grouped: dict[str, dict[str, Any]] = {}
    for log in logs:
        role = log.agent_role or "UNKNOWN"
        group = grouped.setdefault(
            role, {"agent_role": role, **_base_group(), "models_used": set()}
        )
        _add_log(group, log)
        group["models_used"].add(log.model_name)
    result = list(grouped.values())
    for group in result:
        group["models_used"] = sorted(group["models_used"])
    return _finalize_groups(result)


def get_cost_by_employee(
    db: Session, tenant_id: uuid.UUID, month: Optional[str] = None
) -> list[dict[str, Any]]:
    users = {
        user.id: user
        for user in db.query(User).filter(User.tenant_id == tenant_id).all()
    }
    logs = _metered_logs_query(db, tenant_id, month)[0].all()
    grouped: dict[str, dict[str, Any]] = {}
    for log in logs:
        user = users.get(log.user_id) if log.user_id else None
        key = str(log.user_id) if log.user_id else "SYSTEM"
        group = grouped.setdefault(
            key,
            {
                "user_id": key,
                "full_name": user.full_name if user else "Hệ thống / Workflow",
                "email": user.email if user else None,
                "department": user.department if user else (log.department or "ALL"),
                **_base_group(),
            },
        )
        _add_log(group, log)
    return _finalize_groups(list(grouped.values()))


def get_cost_by_department(
    db: Session, tenant_id: uuid.UUID, month: Optional[str] = None
) -> list[dict[str, Any]]:
    logs = _metered_logs_query(db, tenant_id, month)[0].all()
    grouped: dict[str, dict[str, Any]] = {}
    for log in logs:
        department = log.department or "ALL"
        group = grouped.setdefault(
            department, {"department": department, **_base_group()}
        )
        _add_log(group, log)
    return _finalize_groups(list(grouped.values()))


def get_cost_by_workflow(
    db: Session, tenant_id: uuid.UUID, month: Optional[str] = None
) -> list[dict[str, Any]]:
    workflows = {
        workflow.id: workflow
        for workflow in db.query(AgentWorkflow)
        .filter(AgentWorkflow.tenant_id == tenant_id)
        .all()
    }
    logs = _metered_logs_query(db, tenant_id, month)[0].all()
    grouped: dict[str, dict[str, Any]] = {}
    for log in logs:
        workflow = workflows.get(log.workflow_id) if log.workflow_id else None
        key = str(log.workflow_id) if log.workflow_id else "DIRECT_CHAT"
        group = grouped.setdefault(
            key,
            {
                "workflow_id": key,
                "title": workflow.title if workflow else "Tác vụ trực tiếp (Chat)",
                "status": workflow.status if workflow else "COMPLETED",
                "last_active": None,
                **_base_group(),
            },
        )
        _add_log(group, log)
        created_at = log.created_at.isoformat() if log.created_at else None
        if created_at and (
            group["last_active"] is None or created_at > group["last_active"]
        ):
            group["last_active"] = created_at
    return _finalize_groups(list(grouped.values()))


def get_token_statistics(
    db: Session, tenant_id: uuid.UUID, month: Optional[str] = None
) -> dict[str, Any]:
    logs = (
        _metered_logs_query(db, tenant_id, month)[0]
        .order_by(LLMCostLog.created_at.asc())
        .all()
    )
    daily: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    for log in logs:
        date = log.created_at.strftime("%Y-%m-%d")
        daily_group = daily.setdefault(
            date,
            {
                "date": date,
                "prompt_tokens": 0,
                "cached_prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": Decimal("0"),
            },
        )
        daily_group["prompt_tokens"] += log.prompt_tokens
        daily_group["cached_prompt_tokens"] += log.cached_prompt_tokens
        daily_group["completion_tokens"] += log.completion_tokens
        daily_group["total_tokens"] += log.prompt_tokens + log.completion_tokens
        daily_group["cost_usd"] += Decimal(str(log.estimated_cost_usd))

        model_group = models.setdefault(
            log.model_name,
            {
                "model_name": log.model_name,
                "requests": 0,
                "prompt_tokens": 0,
                "cached_prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": Decimal("0"),
            },
        )
        model_group["requests"] += 1
        model_group["prompt_tokens"] += log.prompt_tokens
        model_group["cached_prompt_tokens"] += log.cached_prompt_tokens
        model_group["completion_tokens"] += log.completion_tokens
        model_group["cost_usd"] += Decimal(str(log.estimated_cost_usd))

    daily_list = list(daily.values())
    model_list = list(models.values())
    for item in daily_list + model_list:
        item["cost_usd"] = _money(item["cost_usd"])
    return {"daily_trends": daily_list, "model_distribution": model_list}


def _cost_maps(logs: list[LLMCostLog]) -> dict[str, Any]:
    tenant_total = Decimal("0")
    departments: dict[str, Decimal] = {}
    agents: dict[str, Decimal] = {}
    users: dict[str, Decimal] = {}
    for log in logs:
        cost = Decimal(str(log.estimated_cost_usd))
        tenant_total += cost
        department = log.department or "ALL"
        departments[department] = departments.get(department, Decimal("0")) + cost
        agents[log.agent_role] = agents.get(log.agent_role, Decimal("0")) + cost
        user_key = str(log.user_id) if log.user_id else "SYSTEM"
        users[user_key] = users.get(user_key, Decimal("0")) + cost
    return {
        "TENANT": {"ALL": tenant_total},
        "DEPARTMENT": departments,
        "AGENT": agents,
        "USER": users,
    }


def get_budget_limits_and_alerts(
    db: Session, tenant_id: uuid.UUID, month: Optional[str] = None
) -> dict[str, Any]:
    logs_query, start, end = _metered_logs_query(db, tenant_id, month)
    cost_maps = _cost_maps(logs_query.all())
    budgets = (
        db.query(CostBudget)
        .filter(CostBudget.tenant_id == tenant_id)
        .order_by(CostBudget.scope_type, CostBudget.scope_id)
        .all()
    )
    budget_items: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    for budget in budgets:
        current_spend = cost_maps.get(budget.scope_type, {}).get(
            budget.scope_id, Decimal("0")
        )
        limit = Decimal(str(budget.monthly_budget_usd))
        usage_pct = (
            current_spend / limit * Decimal("100")
            if limit > 0
            else Decimal("0")
        )
        status = "INACTIVE" if not budget.is_active else "NORMAL"
        if budget.is_active and usage_pct >= 100:
            status = "EXCEEDED"
        elif budget.is_active and usage_pct >= budget.alert_threshold_pct:
            status = "WARNING"

        if status in {"EXCEEDED", "WARNING"}:
            severity = "HIGH" if status == "EXCEEDED" else "MEDIUM"
            alerts.append(
                {
                    "id": str(budget.id),
                    "severity": severity,
                    "title": (
                        "Vượt ngân sách" if status == "EXCEEDED"
                        else "Cảnh báo ngưỡng chi phí"
                    ),
                    "message": (
                        f"{budget.scope_type} {budget.scope_id}: "
                        f"${_money(current_spend, 2):.2f} / "
                        f"${budget.monthly_budget_usd:.2f} "
                        f"({_money(usage_pct, 1)}%)."
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        budget_items.append(
            {
                "id": str(budget.id),
                "scope_type": budget.scope_type,
                "scope_id": budget.scope_id,
                "monthly_budget_usd": budget.monthly_budget_usd,
                "alert_threshold_pct": budget.alert_threshold_pct,
                "current_spend_usd": _money(current_spend),
                "usage_pct": _money(usage_pct, 1),
                "status": status,
                "is_active": budget.is_active,
            }
        )

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "budgets": budget_items,
        "alerts": alerts,
        "total_alerts_count": len(alerts),
    }


def _validate_budget_scope(
    db: Session,
    tenant_id: uuid.UUID,
    scope_type: str,
    scope_id: str,
) -> tuple[str, str]:
    normalized_type = scope_type.strip().upper()
    normalized_id = scope_id.strip()
    if normalized_type not in VALID_BUDGET_SCOPES:
        raise ValueError(f"Unsupported scope_type '{scope_type}'")
    if not normalized_id:
        raise ValueError("scope_id must not be empty")
    if normalized_type == "TENANT":
        normalized_id = "ALL"
    elif normalized_type == "DEPARTMENT":
        normalized_id = normalized_id.upper()
        if normalized_id not in VALID_DEPARTMENTS:
            raise ValueError(f"Unsupported department '{scope_id}'")
    elif normalized_type == "AGENT":
        normalized_id = normalized_id.upper()
        if normalized_id not in VALID_AGENT_ROLES:
            raise ValueError(f"Unsupported agent role '{scope_id}'")
    elif normalized_type == "USER":
        try:
            user_id = uuid.UUID(normalized_id)
        except ValueError as exc:
            raise ValueError("USER scope_id must be a UUID") from exc
        exists = db.query(User).filter(
            User.id == user_id, User.tenant_id == tenant_id
        ).first()
        if not exists:
            raise ValueError("USER scope_id does not belong to tenant")
        normalized_id = str(user_id)
    return normalized_type, normalized_id


def upsert_budget_limit(
    db: Session, tenant_id: uuid.UUID, data: Mapping[str, Any]
) -> dict[str, Any]:
    scope_type, scope_id = _validate_budget_scope(
        db,
        tenant_id,
        str(data["scope_type"]),
        str(data["scope_id"]),
    )
    monthly_budget = float(data["monthly_budget_usd"])
    threshold_pct = int(data["alert_threshold_pct"])
    is_active = bool(data.get("is_active", True))
    if monthly_budget <= 0:
        raise ValueError("monthly_budget_usd must be greater than 0")
    if not 1 <= threshold_pct <= 100:
        raise ValueError("alert_threshold_pct must be between 1 and 100")

    budget = db.query(CostBudget).filter(
        CostBudget.tenant_id == tenant_id,
        CostBudget.scope_type == scope_type,
        CostBudget.scope_id == scope_id,
    ).first()
    if not budget:
        budget = CostBudget(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
            monthly_budget_usd=monthly_budget,
            alert_threshold_pct=threshold_pct,
            is_active=is_active,
        )
        db.add(budget)
    else:
        budget.monthly_budget_usd = monthly_budget
        budget.alert_threshold_pct = threshold_pct
        budget.is_active = is_active
    db.commit()
    db.refresh(budget)
    return {
        "id": str(budget.id),
        "scope_type": budget.scope_type,
        "scope_id": budget.scope_id,
        "monthly_budget_usd": budget.monthly_budget_usd,
        "alert_threshold_pct": budget.alert_threshold_pct,
        "is_active": budget.is_active,
    }


def get_model_routing_rules(
    db: Session, tenant_id: uuid.UUID
) -> list[dict[str, Any]]:
    rules = (
        db.query(ModelRoutingRule)
        .filter(ModelRoutingRule.tenant_id == tenant_id)
        .order_by(ModelRoutingRule.task_type, ModelRoutingRule.agent_role)
        .all()
    )
    return [
        {
            "id": str(rule.id),
            "task_type": rule.task_type,
            "agent_role": rule.agent_role,
            "preferred_model": rule.preferred_model,
            "fallback_model": rule.fallback_model,
            "max_tokens": rule.max_tokens,
            "cost_saving_strategy": rule.cost_saving_strategy,
            "is_active": rule.is_active,
        }
        for rule in rules
    ]


def upsert_model_routing_rule(
    db: Session, tenant_id: uuid.UUID, data: Mapping[str, Any]
) -> dict[str, Any]:
    preferred_model = normalize_model_name(str(data["preferred_model"]))
    fallback_model = normalize_model_name(str(data["fallback_model"]))
    if preferred_model not in supported_model_names():
        raise ValueError(f"Legacy model '{preferred_model}' cannot be selected")
    if fallback_model not in supported_model_names():
        raise ValueError(f"Legacy model '{fallback_model}' cannot be selected")

    task_type = str(data["task_type"]).strip().upper()
    agent_role = str(data["agent_role"]).strip().upper()
    strategy = str(data["cost_saving_strategy"]).strip().upper()
    max_tokens = int(data["max_tokens"])
    if agent_role not in VALID_AGENT_ROLES | {"ALL"}:
        raise ValueError(f"Unsupported agent_role '{agent_role}'")
    if strategy not in VALID_ROUTING_STRATEGIES:
        raise ValueError(f"Unsupported cost_saving_strategy '{strategy}'")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than 0")

    rule = None
    rule_id = data.get("id")
    if rule_id:
        try:
            parsed_rule_id = uuid.UUID(str(rule_id))
        except ValueError as exc:
            raise ValueError("id must be a UUID") from exc
        rule = db.query(ModelRoutingRule).filter(
            ModelRoutingRule.tenant_id == tenant_id,
            ModelRoutingRule.id == parsed_rule_id,
        ).first()
        if not rule:
            raise ValueError("Routing rule was not found for tenant")
    else:
        rule = db.query(ModelRoutingRule).filter(
            ModelRoutingRule.tenant_id == tenant_id,
            ModelRoutingRule.task_type == task_type,
            ModelRoutingRule.agent_role == agent_role,
        ).first()

    if not rule:
        rule = ModelRoutingRule(id=uuid.uuid4(), tenant_id=tenant_id)
        db.add(rule)
    rule.task_type = task_type
    rule.agent_role = agent_role
    rule.preferred_model = preferred_model
    rule.fallback_model = fallback_model
    rule.max_tokens = max_tokens
    rule.cost_saving_strategy = strategy
    rule.is_active = bool(data.get("is_active", True))
    db.commit()
    db.refresh(rule)
    return {
        "id": str(rule.id),
        "task_type": rule.task_type,
        "agent_role": rule.agent_role,
        "preferred_model": rule.preferred_model,
        "fallback_model": rule.fallback_model,
        "max_tokens": rule.max_tokens,
        "cost_saving_strategy": rule.cost_saving_strategy,
        "is_active": rule.is_active,
    }


def resolve_model_for_task(
    db: Session,
    tenant_id: uuid.UUID,
    task_type: str,
    agent_role: str,
) -> Optional[dict[str, Any]]:
    """Resolve an active routing rule for a future provider invocation."""
    normalized_task = task_type.strip().upper()
    normalized_role = agent_role.strip().upper()
    rule = db.query(ModelRoutingRule).filter(
        ModelRoutingRule.tenant_id == tenant_id,
        ModelRoutingRule.task_type == normalized_task,
        ModelRoutingRule.is_active.is_(True),
        ModelRoutingRule.agent_role.in_((normalized_role, "ALL")),
    ).order_by(ModelRoutingRule.agent_role.desc()).first()
    if not rule:
        return None
    return {
        "preferred_model": rule.preferred_model,
        "fallback_model": rule.fallback_model,
        "max_tokens": rule.max_tokens,
        "cost_saving_strategy": rule.cost_saving_strategy,
    }
