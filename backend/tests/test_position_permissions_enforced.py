"""Each org-structure box does what it says, for the agents and the approvals they raise.

A box on a position used to be read by the HR section policy and little else: tool access,
knowledge reach, approvals and agent configuration all asked `user.role`, which a box could
not change. Each test here ticks or unticks one box on a company-defined position and
checks the behaviour it controls.
"""

import uuid

import pytest

from app.agents.hr.profile import _employee_profile_reply
from app.api.v1.approvals import _can_approve
from app.core.security import get_password_hash
from app.domains.hr.hr_employee_tools import query_company_users_sql
from app.domains.knowledge.rag_service import user_search_scope
from app.domains.legal.contract_review_store import can_access_contract_review
from app.domains.legal.legal_document_submission import can_approve_legal_documents
from app.domains.platform.approval_access import eligible_approvers
from app.domains.platform.position_service import assign_position, users_with_permission
from app.models.models import AgentWorkflow, ContractReview, Position, Tenant, User, WorkflowApproval
from app.tools.registry import tool_registry


@pytest.fixture()
def staffer(transactional_db_session):
    """A person on a company-defined position that grants nothing yet."""
    db = transactional_db_session
    anchor = db.query(User).filter(User.email == "employee@company.com").one()
    position = Position(
        id=uuid.uuid4(), tenant_id=anchor.tenant_id, parent_id=anchor.position.parent_id,
        name="Chuyên viên thử quyền", slug=f"test-perm-{uuid.uuid4().hex[:8]}",
        permissions=[], grants_all=False, sort_order=999,
    )
    db.add(position)
    user = User(
        id=uuid.uuid4(), tenant_id=anchor.tenant_id, email=f"perm-{uuid.uuid4().hex[:8]}@company.com",
        full_name="Người Thử Quyền", password_hash=get_password_hash("Password123!"),
        role="Employee", department=anchor.department, is_active=True,
    )
    db.add(user)
    db.flush()
    assign_position(db, user, position)
    db.commit()
    return user


def _grant(db, user: User, *codes: str) -> None:
    user.position.permissions = sorted(codes)
    db.commit()


def _login(client, user: User) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": user.email, "password": "Password123!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --------------------------------------------------------------------------- role string


def test_editing_a_positions_boxes_rewrites_its_holders_role(client, ceo_token_headers, staffer, transactional_db_session):
    assert staffer.role == "Employee"
    url = f"/api/v1/positions/{staffer.position_id}"

    granted = client.patch(url, json={"permissions": ["approvals.sign"]}, headers=ceo_token_headers)
    assert granted.status_code == 200, granted.text
    transactional_db_session.refresh(staffer)
    assert staffer.role == "Manager"

    client.patch(url, json={"permissions": []}, headers=ceo_token_headers).raise_for_status()
    transactional_db_session.refresh(staffer)
    assert staffer.role == "Employee"


# --------------------------------------------------------------------------- agent tools


@pytest.mark.parametrize("tool, code", [
    ("generate_legal_document", "legal.document.generate"),
    ("expense_lookup", "finance.expense.view"),
])
def test_gateway_tools_follow_their_box(tool, code, staffer, transactional_db_session):
    definition = tool_registry.get(tool) if hasattr(tool_registry, "get") else next(
        item for item in tool_registry.all() if item.name == tool
    )
    assert not definition.acl.permits(staffer)
    _grant(transactional_db_session, staffer, code)
    assert definition.acl.permits(staffer)


def test_legal_page_drafting_follows_the_same_box(client, staffer, transactional_db_session):
    headers = _login(client, staffer)
    body = {"document_type": "NDA", "output_format": "docx", "fields": {}}
    assert client.post("/api/v1/legal/document-drafts", json=body, headers=headers).status_code == 403
    _grant(transactional_db_session, staffer, "legal.document.generate")
    # Past the permission gate: what is left is the form's own validation.
    assert client.post("/api/v1/legal/document-drafts", json=body, headers=headers).status_code == 422


# --------------------------------------------------------------------------- knowledge


def test_knowledge_reach_follows_its_two_boxes(staffer, transactional_db_session):
    db = transactional_db_session
    scope = user_search_scope(db, staffer)
    assert scope["department"] == staffer.department
    assert scope["can_read_restricted"] is False

    _grant(db, staffer, "knowledge.scope.company", "knowledge.view_restricted")
    scope = user_search_scope(db, staffer)
    assert scope["department"] == "*"
    assert scope["can_read_restricted"] is True


def test_an_admin_role_string_alone_no_longer_opens_restricted_knowledge(staffer, transactional_db_session):
    staffer.role = "Admin"  # the string, without the position behind it
    transactional_db_session.commit()
    assert user_search_scope(transactional_db_session, staffer)["can_read_restricted"] is False


# --------------------------------------------------------------------------- approvals


def _approval(db, requester: User, *, risk: str = "MEDIUM", action: str = "SUPPORT_EMAIL_SEND") -> WorkflowApproval:
    workflow = AgentWorkflow(
        tenant_id=requester.tenant_id, initiator_id=requester.id, title="Thử quyền duyệt",
        status="AWAITING_APPROVAL", current_step=1, dag_plan={},
    )
    db.add(workflow)
    db.flush()
    approval = WorkflowApproval(
        workflow_id=workflow.id, action_type=action, risk_level=risk,
        payload={"requester_id": str(requester.id)}, status="WAITING",
    )
    db.add(approval)
    db.flush()
    return approval


def test_signing_follows_the_two_approval_boxes(staffer, transactional_db_session):
    db = transactional_db_session
    requester = db.query(User).filter(User.email == "employee@company.com").one()
    medium = _approval(db, requester)
    critical = _approval(db, requester, risk="CRITICAL")

    assert not _can_approve(db, staffer, medium)
    _grant(db, staffer, "approvals.sign")
    assert _can_approve(db, staffer, medium)
    assert not _can_approve(db, staffer, critical)  # CRITICAL is the second box's
    assert staffer in users_with_permission(db, staffer.tenant_id, "approvals.sign")

    _grant(db, staffer, "approvals.sign_critical")
    assert _can_approve(db, staffer, critical)


def _signer_in(db, tenant: Tenant, position: Position, name: str) -> User:
    user = User(
        id=uuid.uuid4(), tenant_id=tenant.id, email=f"{name}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=name, password_hash=get_password_hash("Password123!"),
        role="CEO", department="BOARD", is_active=True,
    )
    db.add(user)
    db.flush()
    assign_position(db, user, position)
    db.commit()
    return user


def test_a_request_no_one_else_can_sign_is_shown_to_its_requester_with_a_warning(client, transactional_db_session):
    """The only critical signer escalating a CRITICAL request: it used to wait unseen."""
    db = transactional_db_session
    tenant = Tenant(id=uuid.uuid4(), name="Solo Signer Tenant", domain=f"solo-{uuid.uuid4().hex}.test")
    db.add(tenant)
    db.flush()
    board = Position(
        id=uuid.uuid4(), tenant_id=tenant.id, name="Giám đốc", slug=f"solo-{uuid.uuid4().hex[:8]}",
        permissions=["approvals.sign_critical"], grants_all=False, sort_order=1, is_active=True,
    )
    db.add(board)
    db.flush()
    founder = _signer_in(db, tenant, board, "founder")
    critical = _approval(db, founder, risk="CRITICAL", action="LEGAL_CONTRACT_APPROVAL")
    db.commit()

    assert eligible_approvers(db, critical) == []
    headers = _login(client, founder)
    assert client.get("/api/v1/approvals/pending", headers=headers).json() == []
    sent = client.get("/api/v1/approvals/submitted", headers=headers).json()
    item = next(entry for entry in sent if entry["id"] == str(critical.id))
    assert item["eligible_approver_count"] == 0
    assert "tối quan trọng" in item["warning"]

    # A second holder of the box can now decide it, and the warning goes away.
    partner = _signer_in(db, tenant, board, "partner")
    assert eligible_approvers(db, critical) == [partner]
    item = next(entry for entry in client.get("/api/v1/approvals/submitted", headers=headers).json() if entry["id"] == str(critical.id))
    assert item["eligible_approver_count"] == 1 and item["warning"] is None


def test_legal_documents_and_reviews_follow_their_boxes(staffer, transactional_db_session):
    db = transactional_db_session
    author = db.query(User).filter(User.email == "employee@company.com").one()
    review = ContractReview(
        tenant_id=author.tenant_id, created_by_id=author.id, source="API", document_name="hd.txt",
        content_hash="x", idempotency_key=uuid.uuid4().hex, represented_party="NEUTRAL",
        contract_type="SERVICE_AGREEMENT", contract_text="Điều 1.", result={},
    )
    assert not can_approve_legal_documents(staffer)
    assert not can_access_contract_review(staffer, review)
    _grant(db, staffer, "legal.document.approve", "legal.review.view_all")
    assert can_approve_legal_documents(staffer)
    assert can_access_contract_review(staffer, review)


# --------------------------------------------------------------------------- agent config


def test_configuring_agents_follows_its_box(client, staffer, transactional_db_session):
    headers = _login(client, staffer)
    url = "/api/v1/agents/HR/configuration-options"
    assert client.get(url, headers=headers).status_code == 403
    _grant(transactional_db_session, staffer, "agents.configure")
    assert client.get(url, headers=headers).status_code == 200


def test_the_signed_in_user_carries_their_boxes_for_the_ui(client, staffer, transactional_db_session):
    _grant(transactional_db_session, staffer, "agents.configure")
    me = client.get("/api/v1/users/me", headers=_login(client, staffer)).json()
    assert me["permissions"] == ["agents.configure"]


# --------------------------------------------------------------------------- HR directory


def test_directory_without_its_box_lists_only_yourself(staffer, transactional_db_session):
    db = transactional_db_session
    _grant(db, staffer, "hr.scope.company")
    result = query_company_users_sql(db, actor=staffer, limit=100)
    assert result["scope"] == "SELF"
    assert [item["email"] for item in result["items"]] == [staffer.email]

    _grant(db, staffer, "hr.scope.company", "hr.directory.view")
    result = query_company_users_sql(db, actor=staffer, limit=100)
    assert result["scope"] == "COMPANY"
    assert len(result["items"]) > 1


def test_a_profile_without_the_basic_card_is_answered_not_crashed():
    reply = _employee_profile_reply({
        "employee": {}, "contracts": [], "access": {"allowed_sections": ["CONTRACT"], "purpose": "HR_OPERATIONS"},
    })
    assert "Tra cứu danh bạ nhân sự" in reply
    assert "Hợp đồng được phép xem: **0**" in reply


def test_a_detached_user_still_resolves_permissions_through_the_session(transactional_db_session):
    """A streamed chat reply runs after the request's session let go of the user; the
    tool ACL check used to lazy-load the position then and crash the whole turn."""
    from app.agents.langgraph.engine import role_restricted_tools
    from app.domains.platform.position_service import user_permissions
    from tests.finance_helpers import person

    db = transactional_db_session
    user = person(db, "finance.ledger.view")
    db.expunge(user)
    assert "finance.ledger.view" in user_permissions(db, user)
    restricted = {item["name"] for item in role_restricted_tools(user, ["get_account_balance", "ar_ap_aging"], db)}
    assert restricted == {"ar_ap_aging"}
