"""
Auth service: handles registration, login, and token refresh logic using sync SQLAlchemy Session.
"""

import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select
from fastapi import HTTPException, status

from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.hr_capabilities import default_hr_tools
from app.core.permissions import ROOT_POSITION_SLUG
from app.models.models import Tenant, User, AIAgent, Department
from app.schemas.auth import RegisterRequest, LoginRequest, LoginResponse, UserInToken
from app.services.position_service import ensure_tenant_positions


DEFAULT_AGENTS = [
    {"name": "CEO Agent", "role_code": "CEO", "avatar_emoji": "👔", "description": "Master orchestrator — plans and delegates tasks across all AI employees."},
    {"name": "HR Agent", "role_code": "HR", "avatar_emoji": "🧑‍💼", "description": "Handles leave requests, employee onboarding, and HR policy Q&A."},
    {"name": "Legal Agent", "role_code": "LEGAL", "avatar_emoji": "⚖️", "description": "Reviews contracts, detects risk clauses, generates amended documents."},
    {"name": "IT Agent", "role_code": "IT", "avatar_emoji": "💻", "description": "Resolves technical issues via RAG and auto-creates support tickets."},
    {"name": "Finance Agent", "role_code": "FINANCE", "avatar_emoji": "💰", "description": "OCRs invoices, reconciles PO database, alerts on discrepancies."},
    {"name": "Sales Agent", "role_code": "SALES", "avatar_emoji": "📈", "description": "Looks up inventory, generates PDF quotations, logs leads to CRM."},
    {"name": "Knowledge Agent", "role_code": "KNOWLEDGE", "avatar_emoji": "📚", "description": "Company-wide knowledge base with hybrid RAG search and citations."},
]

DEFAULT_AGENT_TOOLS = {
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


def register_user(db: Session, data: RegisterRequest) -> LoginResponse:
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

    access_token = create_access_token(subject=user.id, role=user.role, tenant_id=tenant.id)
    refresh_token = create_refresh_token(subject=user.id)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserInToken.model_validate(user),
    )


def login_user(db: Session, data: LoginRequest) -> LoginResponse:
    """Authenticate user credentials and return tokens."""
    user = db.query(User).filter(User.email == data.email).first()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    access_token = create_access_token(subject=user.id, role=user.role, tenant_id=user.tenant_id)
    refresh_token = create_refresh_token(subject=user.id)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserInToken.model_validate(user),
    )


def refresh_tokens(db: Session, refresh_token: str) -> LoginResponse:
    """Validate refresh token and issue new access + refresh tokens."""
    payload = decode_token(refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    new_access = create_access_token(subject=user.id, role=user.role, tenant_id=user.tenant_id)
    new_refresh = create_refresh_token(subject=user.id)

    return LoginResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        user=UserInToken.model_validate(user),
    )
