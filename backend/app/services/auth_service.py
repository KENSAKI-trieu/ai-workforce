"""
Auth service: handles registration, login, and token refresh logic using sync SQLAlchemy Session.
"""

import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import NamedTuple, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.gateway_tools import GATEWAY_TOOLS, with_gateway_grants
from app.core.hr_capabilities import default_hr_tools
from app.core.permissions import ROOT_POSITION_SLUG
from app.models.models import Tenant, User, AIAgent, Department, RefreshToken
from app.schemas.auth import RegisterRequest, LoginRequest, LoginResponse, UserInToken
from app.services.position_service import ensure_tenant_positions


class AuthSession(NamedTuple):
    """What the route needs: a body to return and a token to put in the cookie.

    The refresh token is deliberately not part of `LoginResponse`. It travels only in the
    HttpOnly cookie, so nothing on the page -- including injected script -- can read it.
    """

    response: LoginResponse
    refresh_token: str
    refresh_jti: uuid.UUID


DEFAULT_AGENTS = [
    {"name": "CEO Agent", "role_code": "CEO", "avatar_emoji": "👔", "description": "Master orchestrator — plans and delegates tasks across all AI employees."},
    {"name": "HR Agent", "role_code": "HR", "avatar_emoji": "🧑‍💼", "description": "Handles leave requests, employee onboarding, and HR policy Q&A."},
    {"name": "Legal Agent", "role_code": "LEGAL", "avatar_emoji": "⚖️", "description": "Reviews contracts, detects risk clauses, generates amended documents."},
    {"name": "IT Agent", "role_code": "IT", "avatar_emoji": "💻", "description": "Resolves technical issues via RAG and auto-creates support tickets."},
    {"name": "Finance Agent", "role_code": "FINANCE", "avatar_emoji": "💰", "description": "OCRs invoices, reconciles PO database, alerts on discrepancies."},
    {"name": "Sales Agent", "role_code": "SALES", "avatar_emoji": "📈", "description": "Looks up inventory, generates PDF quotations, logs leads to CRM."},
    {"name": "Knowledge Agent", "role_code": "KNOWLEDGE", "avatar_emoji": "📚", "description": "Company-wide knowledge base with hybrid RAG search and citations."},
]

# Capability names dispatched by the deterministic executors, per role. The gateway tool
# names a role needs under LANGGRAPH_ENABLED are composed in below rather than repeated
# here -- a tenant registered through signup used to get capability names only, which left
# every routed agent with an empty gateway toolset and no retrieval at all.
DEFAULT_AGENT_CAPABILITIES = {
    "CEO": ["generate_and_execute_ceo_dag"],
    # Derived, not copied: a hand-maintained list drifted from the executor's dispatch and
    # kept granting names no branch could reach.
    "HR": default_hr_tools(),
    "LEGAL": [
        "audit_contract_risk",
        "compare_contract_versions",
        "check_sensitive_data",
        "check_software_licenses",
        "generate_legal_document",
        "hybrid_rag_search",
    ],
    "IT": ["search_it_kb", "create_jira_ticket"],
    "FINANCE": ["reconcile_po_db"],
    "SALES": ["generate_quotation_pdf"],
    "KNOWLEDGE": ["hybrid_search_documents"],
}

DEFAULT_AGENT_TOOLS = {
    role_code: with_gateway_grants(role_code, capabilities)
    for role_code, capabilities in DEFAULT_AGENT_CAPABILITIES.items()
}


def supported_agent_tools(role_code: str) -> frozenset[str]:
    """Every tool name that changes what this role can do when granted.

    A role's own capabilities, which its executor checks, plus every gateway tool for the
    roles that run through LangGraph: gateway grants beyond the defaults are a per-tenant
    choice. HR never runs through the graph, so gateway tools would do nothing for it --
    as would another role's capability, such as `request_leave` on the Legal agent.
    """
    role = role_code.upper()
    capabilities = frozenset(DEFAULT_AGENT_CAPABILITIES.get(role, ()))
    if role == "HR":
        return capabilities
    return capabilities | GATEWAY_TOOLS


DEFAULT_DEPARTMENTS = [
    ("BOARD", "Ban điều hành"),
    ("HR", "Nhân sự"),
    ("SALES", "Kinh doanh"),
    ("MARKETING", "Marketing"),
    ("FINANCE", "Kế toán & Tài chính"),
    ("LEGAL", "Pháp chế"),
    ("IT", "Công nghệ thông tin"),
]


def ensure_tenant_default_agents(db: Session, tenant_id: uuid.UUID) -> list[AIAgent]:
    """Ensure that default AI agents exist for a given tenant_id. Auto-seed if missing."""
    agents = db.query(AIAgent).filter(AIAgent.tenant_id == tenant_id).all()
    if not agents:
        for agent_data in DEFAULT_AGENTS:
            agent = AIAgent(
                tenant_id=tenant_id,
                name=agent_data["name"],
                role_code=agent_data["role_code"],
                system_prompt=f"You are the {agent_data['name']} for this organization. {agent_data['description']}",
                avatar_emoji=agent_data["avatar_emoji"],
                description=agent_data["description"],
                tools_access=DEFAULT_AGENT_TOOLS[agent_data["role_code"]],
                allowed_actions=DEFAULT_AGENT_TOOLS[agent_data["role_code"]],
            )
            db.add(agent)
        db.commit()
        agents = db.query(AIAgent).filter(AIAgent.tenant_id == tenant_id).all()
    return agents


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """A real bcrypt hash to verify against when the email does not exist.

    Without it, a login for an unknown address returns before bcrypt ever runs and one
    for a known address does not, and the difference is large enough to read off the
    wire -- an oracle that tells an attacker which of your colleagues have accounts.
    """
    return get_password_hash("password-that-authenticates-nobody")


def _client_fingerprint(request: Optional[Request]) -> tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    user_agent = request.headers.get("user-agent")
    client_ip = request.client.host if request.client else None
    return (user_agent[:500] if user_agent else None), client_ip


def _issue_session(
    db: Session,
    user: User,
    *,
    family_id: Optional[uuid.UUID] = None,
    request: Optional[Request] = None,
) -> AuthSession:
    """Mint an access/refresh pair and record the refresh token so it can be revoked."""
    access_token = create_access_token(
        subject=user.id, role=user.role, tenant_id=user.tenant_id
    )
    issued = create_refresh_token(subject=user.id, family_id=family_id)
    user_agent, client_ip = _client_fingerprint(request)
    db.add(
        RefreshToken(
            id=issued.jti,
            user_id=user.id,
            tenant_id=user.tenant_id,
            family_id=issued.family_id,
            expires_at=issued.expires_at,
            user_agent=user_agent,
            client_ip=client_ip,
        )
    )
    # Flushed here so the rotation below can point `replaced_by_id` at a row that exists.
    db.flush()
    return AuthSession(
        response=LoginResponse(
            access_token=access_token,
            user=UserInToken.model_validate(user),
        ),
        refresh_token=issued.token,
        refresh_jti=issued.jti,
    )


def _revoke_family(db: Session, family_id: uuid.UUID, reason: str) -> int:
    """Revoke every live token descended from one login. Returns how many were killed."""
    return (
        db.query(RefreshToken)
        .filter(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .update(
            {"revoked_at": datetime.now(timezone.utc), "revoked_reason": reason},
            synchronize_session=False,
        )
    )


def revoke_user_sessions(db: Session, user_id: uuid.UUID, reason: str) -> int:
    """Revoke every live refresh token belonging to one person."""
    return (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .update(
            {"revoked_at": datetime.now(timezone.utc), "revoked_reason": reason},
            synchronize_session=False,
        )
    )


def register_user(
    db: Session,
    data: RegisterRequest,
    *,
    request: Optional[Request] = None,
) -> AuthSession:
    """Register a new user and seed AI Agents if a new tenant is created."""
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    domain = data.tenant_name.lower().replace(" ", "-") + f"-{str(uuid.uuid4())[:8]}"
    tenant = Tenant(name=data.tenant_name, domain=domain)
    db.add(tenant)
    db.flush()

    for code, name in DEFAULT_DEPARTMENTS:
        db.add(Department(tenant_id=tenant.id, code=code, name=name))

    for agent_data in DEFAULT_AGENTS:
        role_code = agent_data["role_code"]
        agent = AIAgent(
            tenant_id=tenant.id,
            name=agent_data["name"],
            role_code=role_code,
            system_prompt=f"You are the {agent_data['name']} for this organization. {agent_data['description']}",
            avatar_emoji=agent_data["avatar_emoji"],
            description=agent_data["description"],
            tools_access=DEFAULT_AGENT_TOOLS[role_code],
            allowed_actions=DEFAULT_AGENT_TOOLS[role_code],
        )
        db.add(agent)

    # The company gets its own org tree from the first moment, so the founder has
    # something to rename and build on rather than a fixed vocabulary.
    positions = ensure_tenant_positions(db, tenant.id)
    root = positions[ROOT_POSITION_SLUG]

    user = User(
        tenant_id=tenant.id,
        email=data.email,
        full_name=data.full_name,
        password_hash=get_password_hash(data.password),
        # The founder holds the root position, which grants every permission this build
        # defines. The legacy `role` string says "CEO" because that is what the person who
        # creates the company is called, in the org chart and in every guard that still
        # reads the string -- including the workspace deletion check.
        role="CEO",
        department="BOARD",
        position_id=root.id,
    )
    db.add(user)
    db.flush()

    return _issue_session(db, user, request=request)


def login_user(
    db: Session,
    data: LoginRequest,
    *,
    request: Optional[Request] = None,
) -> AuthSession:
    """Authenticate user credentials and return tokens."""
    user = db.query(User).filter(User.email == data.email).first()

    # Hash even when there is no such account, so both answers cost the same.
    stored_hash = user.password_hash if user else _dummy_password_hash()
    password_ok = verify_password(data.password, stored_hash)

    if not user or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    return _issue_session(db, user, request=request)


def refresh_tokens(
    db: Session,
    refresh_token: str,
    *,
    request: Optional[Request] = None,
) -> AuthSession:
    """Rotate a refresh token, refusing anything already used, revoked or expired."""
    payload = decode_token(refresh_token)

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
    )
    if payload.get("type") != "refresh":
        raise invalid
    try:
        jti = uuid.UUID(str(payload.get("jti")))
    except (TypeError, ValueError):
        # Tokens minted before sessions were recorded carry no jti. They cannot be
        # revoked or rotated safely, so they are simply no longer accepted.
        raise invalid

    record = db.query(RefreshToken).filter(RefreshToken.id == jti).first()
    if record is None:
        raise invalid

    if record.revoked_at is not None:
        # The token was already rotated or explicitly revoked, yet someone still holds a
        # copy: the credential exists in two places. Kill the entire family rather than
        # this one row, otherwise the thief and the rightful owner keep rotating past
        # each other and neither is ever locked out.
        _revoke_family(db, record.family_id, reason="REUSE_DETECTED")
        # get_db rolls back on exception, so the revocation is committed before raising.
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token was already used; every session from this login has been revoked",
        )

    if record.expires_at <= datetime.now(timezone.utc):
        raise invalid

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    session = _issue_session(db, user, family_id=record.family_id, request=request)
    record.revoked_at = datetime.now(timezone.utc)
    record.revoked_reason = "ROTATED"
    record.replaced_by_id = session.refresh_jti
    db.flush()
    return session


def change_password(
    db: Session,
    user: User,
    current_password: str,
    new_password: str,
    *,
    request: Optional[Request] = None,
) -> AuthSession:
    """Change a password and end every other session it protected.

    A password change that leaves old sessions running does not actually take anything
    away from whoever prompted it. Every refresh token for this account is revoked, then
    the caller's own device is given a fresh one so the person doing the right thing is
    not the only one logged out.
    """
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    if verify_password(new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new password must be different from the current one",
        )

    user.password_hash = get_password_hash(new_password)
    db.flush()
    revoke_user_sessions(db, user.id, reason="PASSWORD_CHANGED")
    return _issue_session(db, user, request=request)


def logout_user(db: Session, refresh_token: Optional[str]) -> None:
    """End the session behind this refresh token, and every token rotated from it.

    Logging out used to clear a cookie and nothing else, which left the token itself
    valid for the rest of its thirty days. Never raises: a logout that cannot identify
    the session still has to clear the cookie and report success.
    """
    if not refresh_token:
        return
    try:
        payload = decode_token(refresh_token)
    except HTTPException:
        return
    if payload.get("type") != "refresh":
        return
    try:
        family_id = uuid.UUID(str(payload.get("family_id")))
    except (TypeError, ValueError):
        return
    _revoke_family(db, family_id, reason="LOGOUT")
