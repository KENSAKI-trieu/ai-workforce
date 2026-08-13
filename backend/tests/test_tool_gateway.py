from __future__ import annotations

import uuid

from app.core.security import create_internal_tool_token
from app.models.models import AuditLog, LLMCostLog, Task, User
from app.tools.registry import ToolAction, tool_registry


def _audit() -> dict[str, str]:
    return {"correlation_id": str(uuid.uuid4())}


def test_registry_exposes_governance_metadata() -> None:
    definitions = {item.name: item for item in tool_registry.all()}
    assert len(definitions) == 7
    assert definitions["rag_search"].action == ToolAction.READ_ONLY
    assert definitions["create_task"].action == ToolAction.WRITE
    assert definitions["submit_approval_request"].action == ToolAction.EXTERNAL_ACTION
    assert definitions["rag_search"].retry.max_attempts == 3
    assert definitions["create_task"].retry.max_attempts == 1
    assert "tenant_id" in definitions["rag_search"].input_schema.model_json_schema()["properties"]


def test_gateway_rejects_access_jwt(client, ceo_token_headers) -> None:
    response = client.get("/api/v1/internal/tools", headers=ceo_token_headers)
    assert response.status_code == 401


def test_gateway_rejects_tenant_claim_input_mismatch(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    token = create_internal_tool_token(actor)
    response = client.post(
        "/api/v1/internal/tools/rag_search/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input": {
                "tenant_id": str(uuid.uuid4()),
                "audit": _audit(),
                "query": "leave policy",
            }
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant mismatch"


def test_gateway_enforces_acl_from_database(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    response = client.post(
        "/api/v1/internal/tools/expense_lookup/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _audit(),
                "breakdown": "SUMMARY",
            }
        },
    )
    assert response.status_code == 403


def test_gateway_create_task_and_audit(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    correlation_id = str(uuid.uuid4())
    invocation = {
        "input": {
            "tenant_id": str(actor.tenant_id),
            "audit": {"correlation_id": correlation_id, "idempotency_key": f"task-{uuid.uuid4()}"},
            "title": "Review phase 3 registry",
            "assignee_id": str(actor.id),
        }
    }
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/v1/internal/tools/create_task/invoke", headers=headers, json=invocation
    )
    assert response.status_code == 200, response.text
    task_id = response.json()["result"]["task_id"]
    task = transactional_db_session.query(Task).filter(Task.id == uuid.UUID(task_id)).one()
    assert task.tenant_id == actor.tenant_id
    event = transactional_db_session.query(AuditLog).filter(
        AuditLog.tool_name == "create_task",
        AuditLog.resource_type == "TOOL_EXECUTION",
    ).order_by(AuditLog.created_at.desc()).first()
    assert event is not None
    assert event.input_parameters["correlation_id"] == correlation_id
    assert event.actor_user_id == actor.id

    repeated = client.post(
        "/api/v1/internal/tools/create_task/invoke", headers=headers, json=invocation
    )
    assert repeated.status_code == 200
    assert repeated.json()["result"]["task_id"] == task_id
    assert transactional_db_session.query(Task).filter(
        Task.creator_id == actor.id,
        Task.title == "Review phase 3 registry",
    ).count() == 1


def test_gateway_records_model_cost_and_latency(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    token = create_internal_tool_token(actor, agent_role="CEO")
    correlation_id = str(uuid.uuid4())
    response = client.post(
        "/api/v1/internal/tools/model-usage",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "tenant_id": str(actor.tenant_id),
            "correlation_id": correlation_id,
            "agent_role": "CEO",
            "provider": "openai",
            "model": "gpt-4o",
            "prompt_tokens": 100,
            "cached_prompt_tokens": 20,
            "completion_tokens": 25,
            "latency_ms": 321,
            "status": "SUCCESS",
        },
    )
    assert response.status_code == 200, response.text
    usage = transactional_db_session.query(LLMCostLog).filter(
        LLMCostLog.id == uuid.UUID(response.json()["usage_id"])
    ).one()
    assert usage.tenant_id == actor.tenant_id
    assert usage.user_id == actor.id
    assert float(usage.estimated_cost_usd) > 0
    audit = transactional_db_session.query(AuditLog).filter(
        AuditLog.resource_id == str(usage.id),
        AuditLog.tool_name == "model_usage_middleware",
    ).one()
    assert audit.execution_time_ms == 321
    assert audit.input_parameters["correlation_id"] == correlation_id
