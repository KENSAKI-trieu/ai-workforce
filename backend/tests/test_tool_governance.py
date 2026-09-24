"""Governance guarantees of the internal tool gateway.

Separate from test_tool_gateway.py, which covers the happy path and the coarse ACL: these
tests pin the properties that were silently missing -- grants in the right name space, audit
rows that do not re-publish what a read tool just filtered, replay that proves it is a
replay, tokens that name their actor, and an approval gate a requester cannot satisfy alone.
"""

from __future__ import annotations

import ast
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from jose import jwt

from app.api.v1.agents import TOOL_DESCRIPTIONS
from app.api.v1.approvals import _can_approve
from app.core.config import settings
from app.core.gateway_tools import (
    GATEWAY_TOOL_GRANTS,
    GATEWAY_TOOLS,
    effective_tool_grants,
)
from app.core.security import create_internal_tool_token
from app.models.models import AIAgent, AuditLog, Task, User, WorkflowApproval
from app.services.agents.langgraph_engine import LangGraphEngine
from app.domains.platform.auth_service import DEFAULT_AGENT_TOOLS
from app.services.langgraph_approvals import GRAPH_APPROVAL_KIND
from app.tools.executors import search_rag
from app.tools.registry import ToolContext, tool_registry
from app.tools.schemas import RAGSearchInput


def _audit() -> dict[str, str]:
    return {"correlation_id": str(uuid.uuid4())}


def _mutating_audit() -> dict[str, str]:
    return {
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": f"gov-{uuid.uuid4()}",
    }


# ---------------------------------------------------------------------------
# Contract parity and grant coverage
# ---------------------------------------------------------------------------


AI_SERVICE_APP = Path(__file__).resolve().parents[2] / "apps" / "ai-service" / "app"


def _agent_tool_ceilings() -> dict[str, set[str]]:
    """Read each AI-service agent's tool ceiling (agents/<role>/tools.py) from source.

    The two services cannot import one another -- both packages are named `app` -- so the
    ceilings are read as text.
    """
    ceilings: dict[str, set[str]] = {}
    for path in sorted((AI_SERVICE_APP / "agents").glob("*/tools.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            target = getattr(node, "target", None)
            if isinstance(node, ast.AnnAssign) and getattr(target, "id", "") == "TOOLS":
                ceilings[path.parent.name] = set(ast.literal_eval(node.value))
    return ceilings


def test_the_ai_service_keeps_no_copy_of_the_tool_contracts() -> None:
    """It builds its tools from GET /internal/tools; a second copy is what used to drift."""
    if not AI_SERVICE_APP.exists():
        pytest.skip("AI service source is not present in this checkout")
    assert not (AI_SERVICE_APP / "tools" / "schemas.py").exists()
    registry_source = (AI_SERVICE_APP / "tools" / "registry.py").read_text(encoding="utf-8")
    assert "list_tools" in registry_source or "build_langchain_tools" in registry_source
    for item in tool_registry.all():
        assert f'"{item.name}"' not in registry_source, item.name


def test_every_agent_tool_ceiling_names_a_backend_tool() -> None:
    """A ceiling naming a tool the backend does not have would silently never be offered."""
    if not AI_SERVICE_APP.exists():
        pytest.skip("AI service source is not present in this checkout")
    ceilings = _agent_tool_ceilings()
    assert {"legal", "hr", "knowledge"} <= set(ceilings)
    registry_names = {item.name for item in tool_registry.all()}
    for agent, tools in ceilings.items():
        assert tools <= registry_names, (agent, tools - registry_names)


def test_gateway_grant_names_exist_in_registry() -> None:
    registry_names = {item.name for item in tool_registry.all()}
    assert GATEWAY_TOOLS == registry_names
    for role_code, grants in GATEWAY_TOOL_GRANTS.items():
        assert set(grants) <= registry_names, role_code
        # Without retrieval no domain can ground a single answer.
        assert "rag_search" in grants, role_code
        if role_code == "HR":
            # Search only: its other capabilities are HR names checked by its executor.
            assert grants == ("rag_search",)


def test_default_agent_tools_carry_the_gateway_grants() -> None:
    registry_names = {item.name for item in tool_registry.all()}
    for role_code, tools in DEFAULT_AGENT_TOOLS.items():
        granted = set(tools) & registry_names
        assert granted == set(GATEWAY_TOOL_GRANTS.get(role_code, ())), role_code


def test_configuration_api_can_describe_every_gateway_tool() -> None:
    # PATCH /agents/{role_code} rejects any submitted name it cannot describe, which made
    # every seeded non-HR agent unsaveable while the gateway names were missing from it.
    assert GATEWAY_TOOLS <= set(TOOL_DESCRIPTIONS)


# ---------------------------------------------------------------------------
# Audit retention, replay and token claims
# ---------------------------------------------------------------------------


def test_read_tool_result_is_not_persisted_to_the_audit_trail(
    client, transactional_db_session
) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    token = create_internal_tool_token(actor)
    correlation_id = str(uuid.uuid4())
    response = client.post(
        "/api/v1/internal/tools/rag_search/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": {"correlation_id": correlation_id},
                "query": "chinh sach nghi phep",
            }
        },
    )
    assert response.status_code == 200, response.text
    event = transactional_db_session.query(AuditLog).filter(
        AuditLog.tool_name == "rag_search",
        AuditLog.resource_type == "TOOL_EXECUTION",
        AuditLog.status == "SUCCESS",
        AuditLog.input_parameters["correlation_id"].astext == correlation_id,
    ).first()
    assert event is not None
    # /audit is readable by Manager and above, and a retrieval result is the governed
    # document text itself -- restricted chunks included.
    assert event.output_result["result_recorded"] is False
    assert "result" not in event.output_result
    assert event.input_parameters["request_fingerprint"]


def test_mutating_tool_result_is_kept_for_replay(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    audit = _mutating_audit()
    invocation = {
        "input": {
            "tenant_id": str(actor.tenant_id),
            "audit": audit,
            "title": "Replayable request",
        }
    }
    headers = {"Authorization": f"Bearer {token}"}
    first = client.post("/api/v1/internal/tools/create_task/invoke", headers=headers, json=invocation)
    assert first.status_code == 200, first.text
    repeated = client.post("/api/v1/internal/tools/create_task/invoke", headers=headers, json=invocation)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["result"] == first.json()["result"]
    assert transactional_db_session.query(Task).filter(
        Task.creator_id == actor.id,
        Task.title == "Replayable request",
    ).count() == 1


def test_reused_idempotency_key_with_different_arguments_conflicts(
    client, transactional_db_session
) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    headers = {"Authorization": f"Bearer {token}"}
    key = f"gov-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/internal/tools/create_task/invoke",
        headers=headers,
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": {"correlation_id": str(uuid.uuid4()), "idempotency_key": key},
                "title": "Original request",
            }
        },
    )
    assert first.status_code == 200, first.text
    conflicting = client.post(
        "/api/v1/internal/tools/create_task/invoke",
        headers=headers,
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": {"correlation_id": str(uuid.uuid4()), "idempotency_key": key},
                "title": "Something else entirely",
            }
        },
    )
    assert conflicting.status_code == 409, conflicting.text
    assert transactional_db_session.query(Task).filter(
        Task.creator_id == actor.id,
        Task.title == "Something else entirely",
    ).count() == 0


def test_gateway_refuses_a_token_that_declares_no_actor(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    issued = datetime.now(timezone.utc)
    forged = jwt.encode(
        {
            "sub": str(actor.id),
            "tenant_id": str(actor.tenant_id),
            "type": "internal_tool",
            "iss": "ai-workforce-backend",
            "aud": "internal-tool-gateway",
            "jti": str(uuid.uuid4()),
            "exp": issued + timedelta(minutes=5),
            "iat": issued,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    response = client.post(
        "/api/v1/internal/tools/rag_search/invoke",
        headers={"Authorization": f"Bearer {forged}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _audit(),
                "query": "leave policy",
            }
        },
    )
    assert response.status_code == 401


def test_agent_token_is_checked_against_the_agent_configuration(
    client, transactional_db_session
) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    granted = create_internal_tool_token(actor, agent_role="KNOWLEDGE")
    allowed = client.post(
        "/api/v1/internal/tools/rag_search/invoke",
        headers={"Authorization": f"Bearer {granted}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _audit(),
                "query": "chinh sach",
            }
        },
    )
    assert allowed.status_code == 200, allowed.text
    # The KNOWLEDGE agent is granted retrieval only; the gateway refuses the rest even
    # though this actor's own role would permit them.
    refused = client.post(
        "/api/v1/internal/tools/expense_lookup/invoke",
        headers={"Authorization": f"Bearer {granted}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _audit(),
                "breakdown": "SUMMARY",
            }
        },
    )
    assert refused.status_code == 403
    assert "cannot use" in str(refused.json()["detail"])


# ---------------------------------------------------------------------------
# Executor-level policy
# ---------------------------------------------------------------------------


def test_search_rag_applies_the_agent_knowledge_scope(
    monkeypatch, transactional_db_session
) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    captured: dict[str, object] = {}

    def fake_search(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.tools.executors.hybrid_search_documents", fake_search)
    request = RAGSearchInput.model_validate(
        {"tenant_id": str(actor.tenant_id), "audit": _audit(), "query": "chinh sach"}
    )

    agent = SimpleNamespace(knowledge_access=["collection:HR Policies"])
    search_rag(ToolContext(db=transactional_db_session, actor=actor, agent=agent), request)
    assert captured["agent_access"] == ["collection:HR Policies"]

    # A user acting without an AI Employee has no agent scope to narrow to.
    search_rag(ToolContext(db=transactional_db_session, actor=actor, agent=None), request)
    assert captured["agent_access"] is None

    # An agent configured with an empty scope must not read as "unscoped".
    unscoped = SimpleNamespace(knowledge_access=[])
    search_rag(ToolContext(db=transactional_db_session, actor=actor, agent=unscoped), request)
    assert captured["agent_access"] is None


def test_submit_approval_rejects_a_self_named_approver(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    response = client.post(
        "/api/v1/internal/tools/submit_approval_request/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _mutating_audit(),
                "title": "Send the external notice",
                "action_type": "EXTERNAL_ACTION_APPROVAL",
                "payload": {"target": "customer"},
                "approver_id": str(actor.id),
            }
        },
    )
    assert response.status_code == 422
    assert "requester cannot be the approver" in str(response.json()["detail"])


def test_submit_approval_rejects_reserved_payload_keys(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    response = client.post(
        "/api/v1/internal/tools/submit_approval_request/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _mutating_audit(),
                "title": "Approve my own interrupt",
                "action_type": "GENERAL_APPROVAL",
                # `kind` selects which branch of _can_approve governs the request.
                "payload": {"kind": GRAPH_APPROVAL_KIND},
            }
        },
    )
    assert response.status_code == 422
    assert "reserved keys" in str(response.json()["detail"])


def test_submit_approval_rejects_an_expiry_in_the_past(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    response = client.post(
        "/api/v1/internal/tools/submit_approval_request/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _mutating_audit(),
                "title": "Already expired on arrival",
                "action_type": "GENERAL_APPROVAL",
                "payload": {"target": "customer"},
                "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            }
        },
    )
    assert response.status_code == 422


def test_mutating_tool_without_an_idempotency_key_is_a_validation_error(
    client, transactional_db_session
) -> None:
    # The model validator behind this rejection raises ValueError, whose error entry is not
    # JSON serializable; the gateway used to answer 500 for every such rejection.
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    token = create_internal_tool_token(actor)
    response = client.post(
        "/api/v1/internal/tools/create_task/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _audit(),
                "title": "No idempotency key",
            }
        },
    )
    assert response.status_code == 422
    assert "idempotency_key" in str(response.json()["detail"])


def test_tool_listing_hides_what_the_agent_configuration_denies(
    client, transactional_db_session
) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    as_user = client.get(
        "/api/v1/internal/tools",
        headers={"Authorization": f"Bearer {create_internal_tool_token(actor)}"},
    )
    assert as_user.status_code == 200, as_user.text
    as_knowledge_agent = client.get(
        "/api/v1/internal/tools",
        headers={
            "Authorization": f"Bearer {create_internal_tool_token(actor, agent_role='KNOWLEDGE')}"
        },
    )
    assert as_knowledge_agent.status_code == 200, as_knowledge_agent.text
    listed = {item["name"] for item in as_knowledge_agent.json()}
    assert listed == {"rag_search"}
    assert listed < {item["name"] for item in as_user.json()}


def test_an_acl_refusal_is_written_to_the_audit_trail(client, transactional_db_session) -> None:
    # `authorize` raises before the executor, so these refusals used to leave no trace at
    # all -- the events an auditor most wants were the ones missing.
    actor = transactional_db_session.query(User).filter(User.email == "employee@company.com").one()
    response = client.post(
        "/api/v1/internal/tools/expense_lookup/invoke",
        headers={"Authorization": f"Bearer {create_internal_tool_token(actor)}"},
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _audit(),
                "breakdown": "SUMMARY",
            }
        },
    )
    assert response.status_code == 403
    # `created_at` defaults to now(), which Postgres freezes at transaction start, and the
    # session fixture spans the whole module -- ordering by it cannot pick a row out. Every
    # lookup below therefore filters on what actually identifies the event.
    event = transactional_db_session.query(AuditLog).filter(
        AuditLog.tool_name == "expense_lookup",
        AuditLog.actor_user_id == actor.id,
        AuditLog.status == "DENIED",
        AuditLog.input_parameters["stage"].astext == "AUTHORIZATION",
    ).first()
    assert event is not None
    assert "Access denied" in event.error_message


def test_an_agent_grant_refusal_is_audited_under_the_agent_role(
    client, transactional_db_session
) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    response = client.post(
        "/api/v1/internal/tools/expense_lookup/invoke",
        headers={
            "Authorization": f"Bearer {create_internal_tool_token(actor, agent_role='KNOWLEDGE')}"
        },
        json={
            "input": {
                "tenant_id": str(actor.tenant_id),
                "audit": _audit(),
                "breakdown": "SUMMARY",
            }
        },
    )
    assert response.status_code == 403
    event = transactional_db_session.query(AuditLog).filter(
        AuditLog.tool_name == "expense_lookup",
        AuditLog.actor_user_id == actor.id,
        AuditLog.status == "DENIED",
        AuditLog.agent_role == "KNOWLEDGE",
        AuditLog.input_parameters["stage"].astext == "AUTHORIZATION",
    ).first()
    assert event is not None
    assert event.actor_type == "AGENT"
    assert "cannot use" in event.error_message


def test_a_tenant_mismatch_is_audited(client, transactional_db_session) -> None:
    actor = transactional_db_session.query(User).filter(User.email == "admin@company.com").one()
    response = client.post(
        "/api/v1/internal/tools/rag_search/invoke",
        headers={"Authorization": f"Bearer {create_internal_tool_token(actor)}"},
        json={
            "input": {
                "tenant_id": str(uuid.uuid4()),
                "audit": _audit(),
                "query": "leave policy",
            }
        },
    )
    assert response.status_code == 403
    event = transactional_db_session.query(AuditLog).filter(
        AuditLog.tool_name == "rag_search",
        AuditLog.actor_user_id == actor.id,
        AuditLog.status == "DENIED",
        AuditLog.input_parameters["stage"].astext == "TENANT_CHECK",
    ).first()
    assert event is not None


def test_effective_grants_apply_allowed_actions_as_a_narrowing_set() -> None:
    # `allowed_actions` never travelled to the AI service, so a tool removed from it was
    # still bound and still offered to the model.
    assert effective_tool_grants(["rag_search", "create_task"], [], []) == ["create_task", "rag_search"]
    assert effective_tool_grants(["rag_search", "create_task"], ["rag_search"], []) == ["rag_search"]
    assert effective_tool_grants(["rag_search"], ["rag_search"], ["rag_search"]) == []
    assert effective_tool_grants([], ["rag_search"], []) == []
    assert effective_tool_grants(None, None, None) == []


def test_gateway_and_graph_payload_agree_on_the_grant(transactional_db_session) -> None:
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.role_code == "KNOWLEDGE"
    ).first()
    assert agent is not None
    original = (agent.tools_access, agent.allowed_actions, agent.disallowed_actions)
    try:
        agent.tools_access = ["rag_search", "create_task"]
        agent.allowed_actions = ["rag_search"]
        agent.disallowed_actions = []
        transactional_db_session.flush()
        payload = LangGraphEngine._payload(
            db=transactional_db_session,
            user=transactional_db_session.query(User).filter(
                User.email == "admin@company.com"
            ).one(),
            agent=agent,
            conversation_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
        )
        assert payload["allowed_tools"] == ["rag_search"]
        assert "create_task" not in payload["allowed_tools"]
    finally:
        agent.tools_access, agent.allowed_actions, agent.disallowed_actions = original
        transactional_db_session.flush()


def test_a_requester_cannot_approve_their_own_gate(client, transactional_db_session) -> None:
    # A Manager who leaves approver_id empty used to fall through to the unconditional
    # `return True` at the end of _can_approve.
    manager = transactional_db_session.query(User).filter(
        User.email == "it.lead@company.com"
    ).one()
    response = client.post(
        "/api/v1/internal/tools/submit_approval_request/invoke",
        headers={"Authorization": f"Bearer {create_internal_tool_token(manager)}"},
        json={
            "input": {
                "tenant_id": str(manager.tenant_id),
                "audit": _mutating_audit(),
                "title": "Hành động ra ngoài do chính tôi yêu cầu",
                "action_type": "EXTERNAL_ACTION_APPROVAL",
                "payload": {"channel": "email"},
            }
        },
    )
    assert response.status_code == 200, response.text
    approval = transactional_db_session.query(WorkflowApproval).filter(
        WorkflowApproval.id == uuid.UUID(response.json()["result"]["approval_id"])
    ).one()
    assert approval.approver_id is None
    assert _can_approve(transactional_db_session, manager, approval) is False

    executive = transactional_db_session.query(User).filter(
        User.email == "admin@company.com"
    ).one()
    assert _can_approve(transactional_db_session, executive, approval) is True
