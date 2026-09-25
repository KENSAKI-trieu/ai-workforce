"""Integration tests for monthly cost reporting, budgets and routing."""

import uuid
from datetime import datetime, timezone

import pytest

from app.core.security import get_password_hash
from app.models.models import Tenant, User
from app.domains.platform.audit_service import log_llm_cost, resolve_model_for_task


@pytest.fixture(scope="module")
def cost_tenant_user(transactional_db_session):
    unique = uuid.uuid4().hex
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Cost Metering Test Tenant",
        domain=f"cost-{unique}.test",
    )
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"cost-{unique}@example.com",
        full_name="Cost Test CEO",
        password_hash=get_password_hash("Password123!"),
        role="CEO",
        department="BOARD",
        is_active=True,
    )
    transactional_db_session.add_all([tenant, user])
    transactional_db_session.commit()
    return tenant, user


@pytest.fixture(scope="module")
def cost_headers(client, cost_tenant_user):
    _, user = cost_tenant_user
    response = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "Password123!"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_monthly_summary_excludes_previous_month(
    client,
    cost_headers,
    cost_tenant_user,
    transactional_db_session,
):
    tenant, user = cost_tenant_user
    current = log_llm_cost(
        transactional_db_session,
        tenant.id,
        "HR",
        "gemini-2.5-flash",
        prompt_tokens=1000,
        completion_tokens=1000,
        user_id=user.id,
    )
    previous = log_llm_cost(
        transactional_db_session,
        tenant.id,
        "HR",
        "gpt-4o",
        prompt_tokens=1000,
        completion_tokens=1000,
        user_id=user.id,
    )
    now = datetime.now(timezone.utc)
    previous.created_at = (
        datetime(now.year - 1, 12, 15, tzinfo=timezone.utc)
        if now.month == 1
        else datetime(now.year, now.month - 1, 15, tzinfo=timezone.utc)
    )
    transactional_db_session.commit()

    response = client.get("/api/v1/costs/summary", headers=cost_headers)
    assert response.status_code == 200
    summary = response.json()
    assert summary["total_requests"] == 1
    assert summary["total_tokens"] == 2000
    assert summary["total_estimated_cost_usd"] == pytest.approx(0.0028)
    assert summary["estimated_savings_usd"] == pytest.approx(0.0097)
    assert current.usage_source == "PROVIDER"

    breakdown = client.get(
        "/api/v1/costs/by-agent", headers=cost_headers
    ).json()
    assert len(breakdown) == 1
    assert breakdown[0]["requests"] == 1
    assert breakdown[0]["total_cost_usd"] == pytest.approx(
        summary["total_estimated_cost_usd"]
    )


def test_agent_budget_uses_exact_agent_spend(client, cost_headers):
    response = client.post(
        "/api/v1/costs/budgets",
        headers=cost_headers,
        json={
            "scope_type": "AGENT",
            "scope_id": "HR",
            "monthly_budget_usd": 1,
            "alert_threshold_pct": 80,
            "is_active": True,
        },
    )
    assert response.status_code == 200
    budget_data = client.get(
        "/api/v1/costs/budgets-alerts", headers=cost_headers
    ).json()
    budget = next(item for item in budget_data["budgets"] if item["scope_id"] == "HR")
    assert budget["current_spend_usd"] == pytest.approx(0.0028)
    assert budget["usage_pct"] == pytest.approx(0.3)


def test_invalid_budget_is_rejected(client, cost_headers):
    response = client.post(
        "/api/v1/costs/budgets",
        headers=cost_headers,
        json={
            "scope_type": "AGENT",
            "scope_id": "HR",
            "monthly_budget_usd": 0,
            "alert_threshold_pct": 101,
        },
    )
    assert response.status_code == 422


def test_routing_rule_is_validated_and_resolvable(
    client,
    cost_headers,
    cost_tenant_user,
    transactional_db_session,
):
    tenant, _ = cost_tenant_user
    response = client.post(
        "/api/v1/costs/model-routing",
        headers=cost_headers,
        json={
            "task_type": "HR_FAQ",
            "agent_role": "HR",
            "preferred_model": "gemini-2.5-flash",
            "fallback_model": "gpt-4o",
            "max_tokens": 1024,
            "cost_saving_strategy": "LOW_COST",
            "is_active": True,
        },
    )
    assert response.status_code == 200
    resolved = resolve_model_for_task(
        transactional_db_session, tenant.id, "HR_FAQ", "HR"
    )
    assert resolved is not None
    assert resolved["preferred_model"] == "gemini-2.5-flash"

    legacy_response = client.post(
        "/api/v1/costs/model-routing",
        headers=cost_headers,
        json={
            "task_type": "OLD_TASK",
            "agent_role": "HR",
            "preferred_model": "gemini-1.5-flash",
            "fallback_model": "gpt-4o",
            "max_tokens": 1024,
            "cost_saving_strategy": "LOW_COST",
            "is_active": True,
        },
    )
    assert legacy_response.status_code == 422
