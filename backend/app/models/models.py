"""
SQLAlchemy ORM Models for AI Workforce Platform.
All tables defined per enterprise specification (Multi-tenant, Tasks, Agents, Workflows, Approvals, RAG Collections).
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ============================================================
# 1. TENANTS — Multi-tenant organization isolation
# ============================================================
class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Asia/Ho_Chi_Minh"
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="vi")
    data_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=365
    )
    default_model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="gpt-4o"
    )
    notification_settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    security_settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    billing_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    departments: Mapped[list["Department"]] = relationship(
        "Department", back_populates="tenant", cascade="all, delete-orphan"
    )
    ai_agents: Mapped[list["AIAgent"]] = relationship("AIAgent", back_populates="tenant", cascade="all, delete-orphan")
    knowledge_documents: Mapped[list["KnowledgeDocument"]] = relationship(
        "KnowledgeDocument", back_populates="tenant", cascade="all, delete-orphan"
    )
    document_chunks: Mapped[list["DocumentChunk"]] = relationship("DocumentChunk", back_populates="tenant", cascade="all, delete-orphan")
    workflows: Mapped[list["AgentWorkflow"]] = relationship("AgentWorkflow", back_populates="tenant")
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="tenant", cascade="all, delete-orphan")


# ============================================================
# 2. USERS — Employees with RBAC roles
# ============================================================
class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_department_tenant_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="departments")


class Position(Base):
    """A job in the tenant's own org tree.

    `name` is whatever the company calls this job and may be renamed freely. `slug` is
    generated once and never changes, because document ACLs persist role strings as data
    and match them lowercased -- a rename must not silently revoke document access.
    `permissions` holds codes from app.core.permissions and is what guards branch on.
    """

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_position_tenant_slug"),
        Index("idx_positions_tenant_parent", "tenant_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # RESTRICT, not CASCADE: deleting a position that still has children would silently
    # delete a whole branch of the org chart.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Only the root carries this. It means "every permission code this build defines",
    # so adding a new permission in a later release cannot lock a company out of it.
    grants_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_department: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
    parent: Mapped["Position | None"] = relationship(
        "Position", remote_side="Position.id", back_populates="children"
    )
    children: Mapped[list["Position"]] = relationship(
        "Position", back_populates="parent"
    )
    holders: Mapped[list["User"]] = relationship("User", back_populates="position")


class User(Base):
    __tablename__ = "users"
    # `ck_users_role` used to pin role to six literal values. It was dropped when the org
    # tree landed, exactly as `ck_users_department` was dropped when departments became a
    # per-tenant table, because the vocabulary is now tenant data rather than code.
    __table_args__ = (
        Index("idx_users_tenant_dept", "tenant_id", "department"),
        Index("idx_users_manager", "manager_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Legacy capability label, kept while the guards migrate to position permissions.
    # Written from the assigned position; read by everything not yet moved over.
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="Employee")
    department: Mapped[str] = mapped_column(String(50), nullable=False, default="ALL")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="RESTRICT"), nullable=True
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Set when a person's manager was chosen by hand, so re-assigning their position does
    # not overwrite that decision with one derived from the tree.
    manager_is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")
    workflows: Mapped[list["AgentWorkflow"]] = relationship("AgentWorkflow", back_populates="initiator")
    approvals: Mapped[list["WorkflowApproval"]] = relationship("WorkflowApproval", back_populates="approver")
    memories: Mapped[list["UserMemory"]] = relationship("UserMemory", back_populates="user", cascade="all, delete-orphan")
    profile: Mapped["UserProfile | None"] = relationship(
        "UserProfile", back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    manager: Mapped["User | None"] = relationship(
        "User", remote_side="User.id", foreign_keys=[manager_id], back_populates="direct_reports"
    )
    direct_reports: Mapped[list["User"]] = relationship(
        "User", foreign_keys=[manager_id], back_populates="manager"
    )
    position: Mapped["Position | None"] = relationship("Position", back_populates="holders")

    @property
    def position_name(self) -> str | None:
        """What this person's job is called in their own company.

        The UI shows this rather than `role`: `role` is a fixed internal label, while the
        position name is whatever the tenant renamed it to.
        """
        return self.position.name if self.position else None


class RefreshToken(Base):
    """One issued refresh token, so that a session can actually be ended.

    Refresh tokens used to be stateless JWTs: logging out cleared a cookie and nothing
    else, a rotated token stayed valid until it expired, and a stolen one was good for
    thirty days with no way to tell or to stop it. Recording each issued token gives all
    three back -- revocation, expiry of the superseded token, and theft detection.

    `family_id` chains a login to every token rotated from it. Presenting a token that
    was already used means two parties hold the same credential, so the whole family is
    revoked and both are forced to log in again.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("idx_refresh_tokens_user", "user_id"),
        Index("idx_refresh_tokens_family", "family_id"),
    )

    # Equal to the `jti` claim of the JWT handed to the client.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship("User")

    @property
    def is_usable(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at > datetime.now(timezone.utc)


class UserProfile(Base):
    """Personal and employment profile separated from authentication data."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_profile_user"),
        Index("idx_user_profiles_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    phone: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(30))
    bio: Mapped[str | None] = mapped_column(Text)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(255))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(30))
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    job_title: Mapped[str | None] = mapped_column(String(150))
    employee_code: Mapped[str | None] = mapped_column(String(50))
    hire_date: Mapped[date | None] = mapped_column(Date)
    employment_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="FULL_TIME"
    )
    employment_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="OFFICIAL"
    )
    skills: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    certifications: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    experience_summary: Mapped[str | None] = mapped_column(Text)
    employment_history: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    monthly_salary: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    salary_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="profile")


# ============================================================
# 3. AI_AGENTS — AI Employee configuration catalog
# ============================================================
class AIAgent(Base):
    __tablename__ = "ai_agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role_code: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # CEO, HR, LEGAL, IT, FINANCE, SALES, KNOWLEDGE
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Free text an administrator adds on top of the shipped answer prompt for this
    # tenant. Empty for every existing row and every new one, which is what makes it
    # safe: a tenant that sets nothing keeps the shipped prompt byte for byte.
    #
    # It reaches the `answer` slot only. A single text box has no way to express which
    # of the three prompt slots it means, and letting free text into `classifier` or
    # `leave_slot` would let somebody adjusting wording break intent routing or date
    # parsing instead. Per-slot edits are what a plugin package is for.
    prompt_overlay: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), default="gpt-4o")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tools_access: Mapped[dict] = mapped_column(JSONB, default=list)
    allowed_actions: Mapped[dict] = mapped_column(JSONB, default=list)
    disallowed_actions: Mapped[dict] = mapped_column(JSONB, default=list)
    knowledge_access: Mapped[dict] = mapped_column(JSONB, default=list)
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    avatar_emoji: Mapped[str | None] = mapped_column(String(10), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="ai_agents")


# ============================================================
# 4. TASKS — Enterprise Task Management
# ============================================================
class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    ai_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agents.id"), nullable=True
    )
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")  # LOW, MEDIUM, HIGH, URGENT
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="DRAFT"
    )  # DRAFT, PENDING, RUNNING, WAITING_APPROVAL, COMPLETED, FAILED, CANCELLED, OVERDUE
    attachments: Mapped[dict] = mapped_column(JSONB, default=list)
    output_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="tasks")
    creator: Mapped["User"] = relationship("User", foreign_keys=[creator_id])
    assignee: Mapped["User | None"] = relationship("User", foreign_keys=[assignee_id])
    ai_agent: Mapped["AIAgent | None"] = relationship("AIAgent")
    comments: Mapped[list["TaskComment"]] = relationship("TaskComment", back_populates="task", cascade="all, delete-orphan")


class TaskComment(Base):
    __tablename__ = "task_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    task: Mapped["Task"] = relationship("Task", back_populates="comments")
    user: Mapped["User"] = relationship("User")


# ============================================================
# 5. AGENT_WORKFLOWS — Multi-agent task execution sessions
# ============================================================
class AgentWorkflow(Base):
    __tablename__ = "agent_workflows"
    __table_args__ = (
        Index("idx_agent_workflows_tenant_thread", "tenant_id", "thread_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    initiator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="PENDING"
    )  # PENDING, IN_PROGRESS, AWAITING_APPROVAL, COMPLETED, FAILED
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    dag_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="workflows")
    initiator: Mapped["User"] = relationship("User", back_populates="workflows")
    approvals: Mapped[list["WorkflowApproval"]] = relationship("WorkflowApproval", back_populates="workflow", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="workflow")
    llm_cost_logs: Mapped[list["LLMCostLog"]] = relationship("LLMCostLog", back_populates="workflow")
    step_executions: Mapped[list["WorkflowStepExecution"]] = relationship(
        "WorkflowStepExecution", back_populates="workflow", cascade="all, delete-orphan"
    )


class WorkflowStepExecution(Base):
    """Durable, idempotent checkpoint for one workflow step."""

    __tablename__ = "workflow_step_executions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "step_key", name="uq_workflow_step_key"),
        Index("idx_workflow_steps_status", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(100), nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    input_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    output_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow: Mapped["AgentWorkflow"] = relationship(
        "AgentWorkflow", back_populates="step_executions"
    )


class CustomerSupportCase(Base):
    __tablename__ = "customer_support_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_support_case_idempotency"),
        Index("idx_support_cases_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    inbound_body: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[float | None] = mapped_column(Float)
    draft_reply: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[dict] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="QUEUED")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    __table_args__ = (
        UniqueConstraint("support_case_id", "idempotency_key", name="uq_outbound_case_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    support_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_support_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContractReview(Base):
    """One run of the Legal Agent contract reviewer, kept so it can be reopened.

    The analyzer output is stored whole as JSONB rather than normalized: it is
    immutable, its shape is versioned by `review_version`, and normalizing findings
    or clauses into rows would force a migration every time a rule pack changes.
    The reviewer's per-finding decisions are the part that changes, and those live
    in `contract_review_decisions`.
    """

    __tablename__ = "contract_reviews"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_contract_review_idempotency"),
        Index("idx_contract_reviews_tenant_created", "tenant_id", "created_at"),
        Index("idx_contract_reviews_tenant_creator", "tenant_id", "created_by_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # SET NULL, not CASCADE: removing the escalation workflow must not erase the
    # record of what was reviewed and who decided what.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_workflows.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="UPLOAD")
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    represented_party: Mapped[str] = mapped_column(String(20), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(80), nullable=False)
    review_version: Mapped[str] = mapped_column(String(10), nullable=False, default="2.0")
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW")
    total_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contract_text: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN")
    redline_artifact_id: Mapped[str | None] = mapped_column(String(64))
    redline_storage_key: Mapped[str | None] = mapped_column(Text)
    redline_filename: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])
    decisions = relationship(
        "ContractReviewDecision",
        back_populates="review",
        cascade="all, delete-orphan",
    )


class ContractReviewDecision(Base):
    """What a reviewer decided about one finding: accept, reject, or rewrite.

    Rows rather than a JSONB blob on the parent, because this is an audit trail:
    each decision needs its own author and timestamp, two people redlining the same
    review must not overwrite each other, and "what did this person accept" has to
    be answerable. Tenant scoping goes through the parent, as with WorkflowApproval.
    """

    __tablename__ = "contract_review_decisions"
    __table_args__ = (
        UniqueConstraint("review_id", "finding_key", name="uq_contract_review_decision_finding"),
        Index("idx_contract_review_decisions_review", "review_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contract_reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # The positional `finding-N` as it stood when the decision was taken. Kept only
    # so a stored decision can be traced back to what the reviewer saw on screen.
    finding_ref: Mapped[str | None] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    revised_text: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    review = relationship("ContractReview", back_populates="decisions")
    decided_by = relationship("User", foreign_keys=[decided_by_id])


# ============================================================
# 6. WORKFLOW_APPROVALS — Human-in-the-loop gate
# ============================================================
class WorkflowApproval(Base):
    __tablename__ = "workflow_approvals"
    __table_args__ = (
        UniqueConstraint(
            "langgraph_interrupt_id",
            name="uq_workflow_approval_langgraph_interrupt",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_workflows.id", ondelete="CASCADE"), nullable=False
    )
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="MEDIUM")  # LOW, MEDIUM, HIGH, CRITICAL
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="WAITING")
    # WAITING, APPROVED, REJECTED, EXPIRED
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    langgraph_interrupt_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    workflow: Mapped["AgentWorkflow"] = relationship("AgentWorkflow", back_populates="approvals")
    approver: Mapped["User | None"] = relationship("User", back_populates="approvals")


# ============================================================
# 7. USER_MEMORIES — Long-term personalization memory
# ============================================================
class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        Index("idx_user_memories", "user_id", "memory_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    memory_category: Mapped[str] = mapped_column(String(50), nullable=False)
    memory_key: Mapped[str] = mapped_column(String(100), nullable=False)
    memory_value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="memories")


# ============================================================
# 8. HR OPERATIONS - structured employee lifecycle records
# ============================================================
class LeaveBalance(Base):
    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint("user_id", "year", name="uq_leave_balance_user_year"),
        Index("idx_leave_balance_tenant_year", "tenant_id", "year"),
        CheckConstraint(
            "allocated_days >= 0 AND carried_over_days >= 0 AND used_days >= 0 "
            "AND reserved_days >= 0",
            name="ck_leave_balance_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("12.00")
    )
    carried_over_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00")
    )
    used_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00")
    )
    reserved_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User")


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        Index("idx_leave_requests_employee_status", "employee_id", "status"),
        Index("idx_leave_requests_tenant_dates", "tenant_id", "start_date", "end_date"),
        CheckConstraint("requested_days > 0", name="ck_leave_request_days_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_workflows.id", ondelete="CASCADE"), nullable=False
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_approvals.id", ondelete="SET NULL"), nullable=True
    )
    leave_type: Mapped[str] = mapped_column(String(30), nullable=False, default="ANNUAL")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    part_of_day: Mapped[str] = mapped_column(String(20), nullable=False, default="FULL_DAY")
    requested_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="WAITING")
    decision_comment: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    employee: Mapped["User"] = relationship("User", foreign_keys=[employee_id])
    manager: Mapped["User | None"] = relationship("User", foreign_keys=[manager_id])
    workflow: Mapped["AgentWorkflow"] = relationship("AgentWorkflow")
    approval: Mapped["WorkflowApproval | None"] = relationship("WorkflowApproval")


class LeaveLedger(Base):
    __tablename__ = "leave_ledger"
    __table_args__ = (
        Index("idx_leave_ledger_balance_created", "balance_id", "created_at"),
        UniqueConstraint("leave_request_id", "entry_type", name="uq_leave_ledger_request_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leave_balances.id", ondelete="CASCADE"), nullable=False
    )
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leave_requests.id", ondelete="SET NULL"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class HRCalendarEvent(Base):
    __tablename__ = "hr_calendar_events"
    __table_args__ = (
        Index("idx_hr_calendar_tenant_dates", "tenant_id", "start_at", "end_at"),
        UniqueConstraint("source_type", "source_id", name="uq_hr_calendar_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_provider: Mapped[str | None] = mapped_column(String(50))
    external_event_id: Mapped[str | None] = mapped_column(String(255))
    sync_status: Mapped[str] = mapped_column(String(30), nullable=False, default="INTERNAL")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship("User")


class EmploymentContract(Base):
    __tablename__ = "employment_contracts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contract_number", name="uq_contract_tenant_number"),
        Index("idx_contract_tenant_expiry", "tenant_id", "end_date", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    contract_number: Mapped[str] = mapped_column(String(100), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    probation_end_date: Mapped[date | None] = mapped_column(Date)
    signed_by_employee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signed_by_company: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    document_name: Mapped[str | None] = mapped_column(String(255))
    document_url: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    employee: Mapped["User"] = relationship("User", foreign_keys=[employee_id])


class OnboardingCase(Base):
    __tablename__ = "onboarding_cases"
    __table_args__ = (
        Index("idx_onboarding_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_workflows.id", ondelete="CASCADE"), nullable=False
    )
    mentor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    probation_end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="IN_PROGRESS")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    employee: Mapped["User"] = relationship("User", foreign_keys=[employee_id])
    steps: Mapped[list["OnboardingStep"]] = relationship(
        "OnboardingStep", back_populates="onboarding", cascade="all, delete-orphan"
    )


class OnboardingStep(Base):
    __tablename__ = "onboarding_steps"
    __table_args__ = (
        UniqueConstraint("onboarding_id", "step_key", name="uq_onboarding_step_key"),
        Index("idx_onboarding_step_status", "onboarding_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    onboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_cases.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_department: Mapped[str] = mapped_column(String(50), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    onboarding: Mapped["OnboardingCase"] = relationship("OnboardingCase", back_populates="steps")
    assignee: Mapped["User | None"] = relationship("User", foreign_keys=[assignee_id])
    task: Mapped["Task | None"] = relationship("Task")


# ============================================================
# 8. LLM_COST_LOGS — Token usage & cost metering
# ============================================================
class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index("idx_chat_conversation_user_updated", "user_id", "updated_at"),
        UniqueConstraint("thread_id", name="uq_chat_conversation_thread_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    ai_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User")
    ai_agent: Mapped["AIAgent"] = relationship("AIAgent")
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="conversation", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("sender IN ('USER', 'ASSISTANT')", name="ck_chat_message_sender"),
        Index("idx_chat_message_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[dict] = mapped_column(JSONB, default=list)
    tools_executed: Mapped[dict] = mapped_column(JSONB, default=list)
    attachments: Mapped[dict] = mapped_column(JSONB, default=list)
    feedback_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stamped by the application at insert, not by now(): Postgres's now() is the start of
    # the transaction, so messages written in one transaction tied, and the scans that
    # read "the newest message" (the Legal review draft, the contract review tool) picked
    # among them arbitrarily. The server default stays for rows inserted outside the ORM.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    conversation: Mapped["ChatConversation"] = relationship(
        "ChatConversation", back_populates="messages"
    )


class LLMCostLog(Base):
    __tablename__ = "llm_cost_logs"
    __table_args__ = (
        CheckConstraint("prompt_tokens >= 0", name="ck_llm_cost_prompt_tokens"),
        CheckConstraint(
            "completion_tokens >= 0", name="ck_llm_cost_completion_tokens"
        ),
        CheckConstraint(
            "cached_prompt_tokens >= 0 AND cached_prompt_tokens <= prompt_tokens",
            name="ck_llm_cost_cached_tokens",
        ),
        CheckConstraint(
            "usage_source IN ('PROVIDER', 'MANUAL_IMPORT', 'LEGACY_ESTIMATE')",
            name="ck_llm_cost_usage_source",
        ),
        Index("idx_llm_cost_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_workflows.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    department: Mapped[str | None] = mapped_column(String(50), nullable=True)
    agent_role: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(18, 9), nullable=False)
    usage_source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PROVIDER", server_default="PROVIDER"
    )
    pricing_version: Mapped[str] = mapped_column(
        String(30), nullable=False, default="2026-07-31", server_default="2026-07-31"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    workflow: Mapped["AgentWorkflow | None"] = relationship("AgentWorkflow", back_populates="llm_cost_logs")
    user: Mapped["User | None"] = relationship("User")


class CostBudget(Base):
    __tablename__ = "cost_budgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(50), nullable=False, default="TENANT")  # TENANT, DEPARTMENT, AGENT, USER
    scope_id: Mapped[str] = mapped_column(String(100), nullable=False, default="ALL")  # e.g., "HR", "ALL", user_id
    monthly_budget_usd: Mapped[float] = mapped_column(Float, nullable=False, default=500.0)
    alert_threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModelRoutingRule(Base):
    __tablename__ = "model_routing_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "HR_FAQ", "LEGAL_CONTRACT", "IT_TICKET", "GENERAL_CHAT"
    agent_role: Mapped[str] = mapped_column(String(50), nullable=False, default="ALL")
    preferred_model: Mapped[str] = mapped_column(String(100), nullable=False, default="gpt-3.5-turbo")
    fallback_model: Mapped[str] = mapped_column(String(100), nullable=False, default="gpt-4o")
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)
    cost_saving_strategy: Mapped[str] = mapped_column(String(50), default="BALANCED")  # LOW_COST, BALANCED, HIGH_PERFORMANCE
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )



# ============================================================
# 9. KNOWLEDGE DOCUMENTS & CHUNKS — governed pgvector knowledge store
# ============================================================
class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "document_id", "version", name="uq_knowledge_document_version"
        ),
        CheckConstraint(
            "processing_status IN ('uploaded', 'parsing', 'chunking', 'embedding', 'indexing', 'ready', 'failed')",
            name="ck_knowledge_documents_processing_status",
        ),
        CheckConstraint(
            "processing_progress >= 0 AND processing_progress <= 100",
            name="ck_knowledge_documents_processing_progress",
        ),
        Index("idx_knowledge_documents_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(String(100), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_title: Mapped[str] = mapped_column(String(255), nullable=False)
    collection_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="General Knowledge"
    )
    department: Mapped[str] = mapped_column(String(50), nullable=False, default="ALL")
    document_type: Mapped[str] = mapped_column(String(50), nullable=False, default="knowledge")
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploaded"
    )
    processing_progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    processing_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    processing_checkpoint: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploaded"
    )
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidentiality: Mapped[str] = mapped_column(
        String(30), nullable=False, default="internal"
    )
    allowed_roles: Mapped[list[str]] = mapped_column(JSONB, default=list)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunking_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "mode": "standard",
            "chunk_size": 700,
            "chunk_overlap": 80,
            "parent_chunk_size": 1024,
        },
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="knowledge_documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="knowledge_document"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'inactive', 'archived')",
            name="ck_doc_chunks_status",
        ),
        CheckConstraint(
            "confidentiality IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_doc_chunks_confidentiality",
        ),
        Index(
            "idx_doc_chunks_governance",
            "tenant_id",
            "status",
            "department_access",
            "effective_date",
        ),
        Index("idx_doc_chunks_document", "tenant_id", "document_id"),
        Index(
            "idx_doc_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        UniqueConstraint(
            "knowledge_document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=True,
    )
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_type: Mapped[str] = mapped_column(String(50), default="knowledge")
    version: Mapped[str] = mapped_column(String(50), default="1.0")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    confidentiality: Mapped[str] = mapped_column(String(30), default="internal")
    allowed_roles: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    collection_name: Mapped[str] = mapped_column(String(100), default="General Knowledge")
    department_access: Mapped[str] = mapped_column(String(50), default="ALL")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_status: Mapped[str] = mapped_column(String(20), default="pending")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    dense_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="document_chunks")
    knowledge_document: Mapped["KnowledgeDocument | None"] = relationship(
        "KnowledgeDocument", back_populates="chunks"
    )


# ============================================================
# 10. AUDIT_LOGS — Full agent action trail
# ============================================================
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_workflow", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SYSTEM"
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_workflows.id"), nullable=True
    )
    agent_role: Mapped[str] = mapped_column(String(50), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str | None] = mapped_column(String(150), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    before_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SUCCESS"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    workflow: Mapped["AgentWorkflow | None"] = relationship("AgentWorkflow", back_populates="audit_logs")
    actor_user: Mapped["User | None"] = relationship("User")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("idx_notifications_user_created", "user_id", "created_at"),
        Index("idx_notifications_user_unread", "user_id", "is_read"),
        UniqueConstraint(
            "tenant_id", "user_id", "dedup_key", name="uq_notification_dedup"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="INFO"
    )
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str] = mapped_column(
        String(30), nullable=False, default="IN_APP"
    )
    delivery_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DELIVERED"
    )
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship("User")


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_notification_preference_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    enabled_event_types: Mapped[dict] = mapped_column(JSONB, default=list)
    enabled_channels: Mapped[dict] = mapped_column(JSONB, default=list)
    quiet_hours: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User")


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "display_name", name="uq_integration_name"
        ),
        Index("idx_integrations_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(50), nullable=False)
    credential_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    permissions: Mapped[dict] = mapped_column(JSONB, default=list)
    allowed_resources: Mapped[dict] = mapped_column(JSONB, default=list)
    allowed_agent_roles: Mapped[dict] = mapped_column(JSONB, default=list)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="CONFIGURED"
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    created_by: Mapped["User"] = relationship("User")
    usage_logs: Mapped[list["IntegrationUsageLog"]] = relationship(
        "IntegrationUsageLog",
        back_populates="connection",
        cascade="all, delete-orphan",
    )


class IntegrationUsageLog(Base):
    __tablename__ = "integration_usage_logs"
    __table_args__ = (
        Index("idx_integration_usage_created", "connection_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    agent_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    connection: Mapped["IntegrationConnection"] = relationship(
        "IntegrationConnection", back_populates="usage_logs"
    )
    actor_user: Mapped["User | None"] = relationship("User")


# ============================================================
# 30. TENANT_PLUGIN_INSTALLS — prompt/skill packages enabled per tenant
# ============================================================
class TenantPluginInstall(Base):
    """One installed plugin package for one tenant.

    The manifest itself stays on disk and is the single source of truth for what a
    plugin does; this row only records that a tenant opted into it. ``plugin_version``
    is the version that was installed, kept so a package edited on disk afterwards can
    be reported as drifted instead of changing a tenant's behaviour unannounced.
    """

    __tablename__ = "tenant_plugin_installs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "plugin_name", name="uq_tenant_plugin"),
        Index("idx_tenant_plugin_role", "tenant_id", "target_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    plugin_name: Mapped[str] = mapped_column(String(100), nullable=False)
    plugin_version: Mapped[str] = mapped_column(String(50), nullable=False)
    target_role: Mapped[str] = mapped_column(String(50), nullable=False)
    installed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ============================================================
# 31. TENANT_PLUGINS — plugin packages a tenant authors itself
# ============================================================
class TenantPlugin(Base):
    """A plugin package written by a tenant's own administrators.

    The packages shipped in `backend/plugins/` stay on disk: they are versioned with
    the code, reviewed like code, and shared by every tenant. These are the other kind
    -- authored in the product, owned by one tenant, and editable without a release.

    ``source_yaml`` is the manifest exactly as the author typed it, and it is the single
    source of truth. It is validated by the same parser as a disk package before it is
    ever stored, so an invalid manifest cannot reach the database, and it is re-parsed
    when read rather than being stored twice in two shapes that could disagree.
    """

    __tablename__ = "tenant_plugins"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_plugin_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_role: Mapped[str] = mapped_column(String(50), nullable=False)
    source_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
