"""
Database Initialization & Seeding Script for AI Workforce Platform.
Creates tables and seeds initial data: Tenant, Users, 7 AI Agents, and Knowledge Documents.
"""

import sys
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from app.core.database import sync_engine, Base, SyncSessionLocal
from app.core.config import settings
from app.core.security import get_password_hash
from app.core.hr_capabilities import (
    HR_CONFIGURATION_VERSION,
    HR_RETIRED_TOOLS,
)
from app.services.auth_service import DEFAULT_AGENT_TOOLS
from app.services.position_service import backfill_tenant_user_positions
from app.models.models import Tenant, User, AIAgent, Department, DocumentChunk, UserMemory, AgentWorkflow, WorkflowApproval, AuditLog, LLMCostLog, Task, TaskComment, LeaveBalance

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("init_db")

# Deterministic 1536-dim vector generator helper for fallback RAG embedding
def generate_fallback_embedding(text_content: str, dim: int = 1536) -> list[float]:
    import hashlib
    import math
    vec = []
    text_bytes = text_content.encode("utf-8")
    for i in range(dim):
        h = hashlib.sha256(text_bytes + str(i).encode()).digest()
        val = (int.from_bytes(h[:4], "big") / (2**32 - 1)) * 2.0 - 1.0
        vec.append(val)
    # Normalize vector to unit length
    norm = math.sqrt(sum(x*x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def init_db():
    logger.info("Initializing database schema...")
    seed_password = settings.SEED_DEFAULT_PASSWORD or secrets.token_urlsafe(32)
    if not settings.SEED_DEFAULT_PASSWORD:
        logger.warning(
            "SEED_DEFAULT_PASSWORD is not set; generated demo accounts will use "
            "an unknown random password. Set it before the first initialization "
            "if interactive demo login is required."
        )
    
    # Enable pgvector extension if PostgreSQL
    with sync_engine.connect() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.commit()
            logger.info("pgvector extension verified/enabled.")
        except Exception as e:
            logger.warning(f"Could not execute CREATE EXTENSION vector (might be sqlite or restricted): {e}")

    # Create all tables
    Base.metadata.create_all(bind=sync_engine)
    logger.info("Database tables created successfully.")

    # Run schema migrations for missing columns on existing tables
    migrations = [
        "ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS allowed_actions JSONB DEFAULT '[]'::jsonb",
        "ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS disallowed_actions JSONB DEFAULT '[]'::jsonb",
        "ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS knowledge_access JSONB DEFAULT '[]'::jsonb",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS processing_progress INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS processing_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS collection_name VARCHAR(100) NOT NULL DEFAULT 'General Knowledge'",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS processing_checkpoint VARCHAR(20) NOT NULL DEFAULT 'uploaded'",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS parsed_text TEXT",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS chunking_config JSONB NOT NULL DEFAULT '{\"mode\": \"standard\", \"chunk_size\": 700, \"chunk_overlap\": 80, \"parent_chunk_size\": 1024}'::jsonb",
        "UPDATE knowledge_documents SET processing_checkpoint = 'ready' WHERE processing_status = 'ready' AND processing_checkpoint = 'uploaded'",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS document_id VARCHAR(100)",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS collection_name VARCHAR(100) DEFAULT 'General Knowledge'",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS document_title VARCHAR(255)",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS document_type VARCHAR(50) NOT NULL DEFAULT 'knowledge'",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS version VARCHAR(50) NOT NULL DEFAULT '1.0'",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS effective_date DATE",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS expiration_date DATE",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS confidentiality VARCHAR(30) NOT NULL DEFAULT 'internal'",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS allowed_roles JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS source_file VARCHAR(255)",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section_title VARCHAR(500)",
        "ALTER TABLE document_chunks ALTER COLUMN section_title TYPE TEXT",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page INTEGER",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS knowledge_document_id UUID",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_start INTEGER",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_end INTEGER",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_text TEXT",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(255)",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(100)",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_status VARCHAR(20) NOT NULL DEFAULT 'pending'",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(768)",
        "UPDATE document_chunks SET document_title = COALESCE(document_title, document_name), source_file = COALESCE(source_file, document_name), section_title = COALESCE(section_title, metadata->>'section_title', 'Chunk ' || chunk_index)",
        "ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS ck_doc_chunks_status",
        "ALTER TABLE document_chunks ADD CONSTRAINT ck_doc_chunks_status CHECK (status IN ('draft', 'active', 'inactive', 'archived'))",
        "ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS ck_doc_chunks_confidentiality",
        "ALTER TABLE document_chunks ADD CONSTRAINT ck_doc_chunks_confidentiality CHECK (confidentiality IN ('public', 'internal', 'confidential', 'restricted'))",
        "CREATE INDEX IF NOT EXISTS idx_doc_chunks_governance ON document_chunks (tenant_id, status, department_access, effective_date)",
        "CREATE INDEX IF NOT EXISTS idx_doc_chunks_document ON document_chunks (tenant_id, document_id)",
        "CREATE INDEX IF NOT EXISTS idx_doc_chunks_content_hash ON document_chunks (tenant_id, document_id, content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops)",
        "CREATE INDEX IF NOT EXISTS idx_doc_chunks_content_fts ON document_chunks USING gin (to_tsvector('simple', content))",
        "ALTER TABLE workflow_approvals ADD COLUMN IF NOT EXISTS risk_level VARCHAR(20) DEFAULT 'MEDIUM'",
        "ALTER TABLE workflow_approvals ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE workflow_approvals ADD COLUMN IF NOT EXISTS comments TEXT",
        "ALTER TABLE agent_workflows ADD COLUMN IF NOT EXISTS title VARCHAR(255) DEFAULT 'Workflow Execution'",
        "ALTER TABLE agent_workflows ADD COLUMN IF NOT EXISTS dag_plan JSONB",
        "ALTER TABLE agent_workflows ADD COLUMN IF NOT EXISTS thread_id VARCHAR(255)",
        "ALTER TABLE agent_workflows ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS tool_name VARCHAR(100) DEFAULT 'general'",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS input_parameters JSONB",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS execution_time_ms INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS department VARCHAR(50) DEFAULT 'ALL'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT",
        # Both vocabularies are tenant data now: departments have their own table, and
        # roles are position slugs the company defines. Re-adding either CHECK here would
        # undo the migration on the next boot.
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role",
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_department",
        "ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS configuration_version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE llm_cost_logs ADD COLUMN IF NOT EXISTS cached_prompt_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE llm_cost_logs ADD COLUMN IF NOT EXISTS usage_source VARCHAR(30) NOT NULL DEFAULT 'LEGACY_ESTIMATE'",
        "ALTER TABLE llm_cost_logs ADD COLUMN IF NOT EXISTS pricing_version VARCHAR(30) NOT NULL DEFAULT 'legacy'",
        "ALTER TABLE llm_cost_logs ALTER COLUMN estimated_cost_usd TYPE NUMERIC(18, 9)",
        "ALTER TABLE llm_cost_logs ALTER COLUMN usage_source SET DEFAULT 'PROVIDER'",
        "ALTER TABLE llm_cost_logs ALTER COLUMN pricing_version SET DEFAULT '2026-07-31'",
        "ALTER TABLE llm_cost_logs DROP CONSTRAINT IF EXISTS ck_llm_cost_prompt_tokens",
        "ALTER TABLE llm_cost_logs ADD CONSTRAINT ck_llm_cost_prompt_tokens CHECK (prompt_tokens >= 0)",
        "ALTER TABLE llm_cost_logs DROP CONSTRAINT IF EXISTS ck_llm_cost_completion_tokens",
        "ALTER TABLE llm_cost_logs ADD CONSTRAINT ck_llm_cost_completion_tokens CHECK (completion_tokens >= 0)",
        "ALTER TABLE llm_cost_logs DROP CONSTRAINT IF EXISTS ck_llm_cost_cached_tokens",
        "ALTER TABLE llm_cost_logs ADD CONSTRAINT ck_llm_cost_cached_tokens CHECK (cached_prompt_tokens >= 0 AND cached_prompt_tokens <= prompt_tokens)",
        "ALTER TABLE llm_cost_logs DROP CONSTRAINT IF EXISTS ck_llm_cost_usage_source",
        "ALTER TABLE llm_cost_logs ADD CONSTRAINT ck_llm_cost_usage_source CHECK (usage_source IN ('PROVIDER', 'MANUAL_IMPORT', 'LEGACY_ESTIMATE'))",
        "CREATE INDEX IF NOT EXISTS idx_llm_cost_tenant_created ON llm_cost_logs (tenant_id, created_at)",
    ]
    for statement in migrations:
        try:
            with sync_engine.begin() as conn:
                conn.execute(text(statement))
        except Exception as e:
            logger.warning(f"Migration statement skipped: {statement} - Error: {e}")
    logger.info("Applied comprehensive schema migrations and constraints across all database tables.")

    db = SyncSessionLocal()
    try:
        # 1. Seed Tenant
        tenant = db.query(Tenant).filter(Tenant.domain == "acme.com").first()
        if not tenant:
            tenant = Tenant(
                id=uuid.uuid4(),
                name="Acme Enterprise Corporation",
                domain="acme.com",
            )
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info(f"Seeded Tenant: {tenant.name} ({tenant.id})")
        else:
            logger.info(f"Tenant already exists: {tenant.name}")

        for code, name in [
            ("BOARD", "Ban điều hành"),
            ("HR", "Nhân sự"),
            ("SALES", "Kinh doanh"),
            ("MARKETING", "Marketing"),
            ("FINANCE", "Kế toán & Tài chính"),
            ("LEGAL", "Pháp chế"),
            ("IT", "Công nghệ thông tin"),
        ]:
            if not db.query(Department).filter(
                Department.tenant_id == tenant.id,
                Department.code == code,
            ).first():
                db.add(Department(tenant_id=tenant.id, code=code, name=name))
        db.commit()

        # 2. Seed Users
        users_data = [
            {
                "email": "admin@company.com",
                "full_name": "Trần Văn CEO",
                "role": "CEO",
                "department": "BOARD",
                "password": seed_password,
            },
            {
                "email": "hr.manager@company.com",
                "full_name": "Nguyễn Thị HR",
                "role": "Manager",
                "department": "HR",
                "password": seed_password,
            },
            {
                "email": "employee@company.com",
                "full_name": "Lê Văn Nhẫn",
                "role": "Employee",
                "department": "IT",
                "password": seed_password,
            },
        ]

        for udata in users_data:
            user = db.query(User).filter(User.email == udata["email"]).first()
            if not user:
                user = User(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    email=udata["email"],
                    full_name=udata["full_name"],
                    password_hash=get_password_hash(udata["password"]),
                    role=udata["role"],
                    department=udata["department"],
                )
                db.add(user)
                logger.info(f"Seeded User: {udata['email']} ({udata['role']})")
        db.commit()

        # Get employee user for memories
        employee_user = db.query(User).filter(User.email == "employee@company.com").first()
        if employee_user:
            # Seed Leave Balance Memory
            mem = db.query(UserMemory).filter(
                UserMemory.user_id == employee_user.id,
                UserMemory.memory_key == "leave_balance"
            ).first()
            if not mem:
                mem = UserMemory(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    user_id=employee_user.id,
                    memory_category="hr",
                    memory_key="leave_balance",
                    memory_value=json.dumps({"total_days": 12, "used_days": 2, "remaining_days": 10}),
                    confidence_score=1.0,
                )
                db.add(mem)
            else:
                mem.memory_value = json.dumps({"total_days": 12, "used_days": 2, "remaining_days": 10})
            balance = db.query(LeaveBalance).filter(
                LeaveBalance.user_id == employee_user.id,
                LeaveBalance.year == datetime.now(timezone.utc).year,
            ).first()
            if not balance:
                balance = LeaveBalance(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    user_id=employee_user.id,
                    year=datetime.now(timezone.utc).year,
                )
                db.add(balance)
            balance.allocated_days = 12
            balance.carried_over_days = 0
            balance.used_days = 2
            balance.reserved_days = 0
            db.commit()

        # 3. Seed AI Agents catalog
        agents_data = [
            {
                "role_code": "CEO",
                "name": "CEO Master Agent",
                "avatar_emoji": "👔",
                "model_name": "gpt-4o",
                "description": "Điều phối công việc đa Agent, lập kế hoạch DAG và tổng hợp báo cáo chiến lược.",
                "system_prompt": (
                    "You are the Chief Executive Officer (CEO) AI Agent of this digital enterprise.\n"
                    "Your goal is to analyze user requests, decompose them into DAG tasks, delegate to HR, Legal, IT, Finance, Sales, and Knowledge agents, and synthesize final summaries."
                ),
            },
            {
                "role_code": "HR",
                "name": "HR AI Employee",
                "avatar_emoji": "🧑‍💼",
                "model_name": "gpt-4o",
                "description": "Quản lý nhân sự, tra cứu số ngày nghỉ phép, lập đơn nghỉ phép và giải đáp chính sách công ty.",
                "system_prompt": (
                    "You are the Human Resources AI Agent.\n"
                    "You assist employees with checking leave balances, submitting leave requests, employee records, and company policies.\n"
                    "Rules:\n"
                    "- Check employee leave balance before requesting leave.\n"
                    "- Generate a Manager Approval Card when an action requires manager consent."
                ),
            },
            {
                "role_code": "KNOWLEDGE",
                "name": "Knowledge Base AI",
                "avatar_emoji": "📚",
                "model_name": "gpt-4o",
                "description": "Truy xuất kho tri thức doanh nghiệp, trả lời chính xác có kèm trích dẫn tài liệu nguồn (Citations).",
                "system_prompt": (
                    "You are the Knowledge Base AI Agent.\n"
                    "Answer employee questions accurately using retrieved company documents.\n"
                    "Always include explicit inline citations [Citation: Document_Name, Section Title]."
                ),
            },
            {
                "role_code": "LEGAL",
                "name": "Legal Counsel AI",
                "avatar_emoji": "⚖️",
                "model_name": "gpt-4o",
                "description": "Thẩm định hợp đồng, rà soát điều khoản rủi ro và xuất tài liệu chỉnh sửa.",
                "system_prompt": "You are the Legal Counsel AI Agent. Review contracts, highlight risk clauses, and suggest redlines.",
            },
            {
                "role_code": "IT",
                "name": "IT Support AI",
                "avatar_emoji": "💻",
                "model_name": "gpt-4o",
                "description": "Hỗ trợ sự cố kỹ thuật, cấp quyền truy cập VPN/email và tạo Ticket Jira tự động.",
                "system_prompt": "You are the IT Support AI Agent. Provide technical help and create Jira tickets when needed.",
            },
            {
                "role_code": "FINANCE",
                "name": "Finance & Accounting AI",
                "avatar_emoji": "💰",
                "model_name": "gpt-4o",
                "description": "Xử lý OCR hóa đơn, đối chiếu PO database và cảnh báo bất thường tài chính.",
                "system_prompt": "You are the Finance AI Agent. Audit invoices, extract details, and flag financial anomalies.",
            },
            {
                "role_code": "SALES",
                "name": "Sales & CRM AI",
                "avatar_emoji": "📈",
                "model_name": "gpt-4o",
                "description": "Tra cứu danh mục sản phẩm, tạo báo giá PDF và cập nhật lead vào CRM.",
                "system_prompt": "You are the Sales AI Agent. Assist with inventory search, PDF quote generation, and lead management.",
            },
        ]

        # Seeded grants come from the same map the signup path uses, so a demo tenant and a
        # registered tenant cannot disagree about what a role may call. Gateway tool names
        # are composed in by DEFAULT_AGENT_TOOLS; HR deliberately gets none, because the
        # backend routes that role around the graph entirely.
        default_agent_tools = DEFAULT_AGENT_TOOLS
        legacy_tools = ["query_leave_balance", "request_leave", "hybrid_rag_search"]
        for adata in agents_data:
            agent = db.query(AIAgent).filter(
                AIAgent.tenant_id == tenant.id,
                AIAgent.role_code == adata["role_code"]
            ).first()
            if not agent:
                agent = AIAgent(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    role_code=adata["role_code"],
                    name=adata["name"],
                    avatar_emoji=adata["avatar_emoji"],
                    model_name=adata["model_name"],
                    description=adata["description"],
                    system_prompt=adata["system_prompt"],
                    is_active=True,
                    tools_access=default_agent_tools[adata["role_code"]],
                    allowed_actions=default_agent_tools[adata["role_code"]],
                    configuration_version=HR_CONFIGURATION_VERSION,
                )
                db.add(agent)
                logger.info(f"Seeded AI Agent: {adata['role_code']} ({adata['name']})")
            elif agent.tools_access == legacy_tools and adata["role_code"] != "HR":
                agent.tools_access = default_agent_tools[adata["role_code"]]
                agent.allowed_actions = default_agent_tools[adata["role_code"]]
                agent.configuration_version = HR_CONFIGURATION_VERSION
            elif (agent.configuration_version or 1) < HR_CONFIGURATION_VERSION:
                denied = set(agent.disallowed_actions or [])
                additions = set(default_agent_tools[adata["role_code"]]) - denied
                tools = set(agent.tools_access or []) | additions
                allowed = set(agent.allowed_actions or []) | additions
                if adata["role_code"] == "HR":
                    # Stamping the current version here tells the executor's capability
                    # migration it has nothing left to do, so the retired names have to be
                    # swept now or they would survive forever on this row.
                    tools -= HR_RETIRED_TOOLS
                    allowed -= HR_RETIRED_TOOLS
                    denied -= HR_RETIRED_TOOLS
                    agent.disallowed_actions = sorted(denied)
                agent.tools_access = sorted(tools)
                agent.allowed_actions = sorted(allowed)
                agent.configuration_version = HR_CONFIGURATION_VERSION
        db.commit()

        # 4. Seed Knowledge Base Documents & Vector Chunks
        sample_docs = [
            {
                "document_name": "Chinh_sach_Nghi_phep_2025.md",
                "department_access": "ALL",
                "chunks": [
                    {
                        "chunk_index": 0,
                        "content": (
                            "# Quy Định Nghỉ Phép Năm 2025\n"
                            "1. Mỗi nhân viên chính thức có **12 ngày nghỉ phép hưởng nguyên lương** mỗi năm.\n"
                            "2. Nhân viên có thâm niên từ 3 năm trở lên được cộng thêm 1 ngày phép cho mỗi năm làm việc tiếp theo.\n"
                            "3. Đơn nghỉ phép từ 1 đến 2 ngày cần xin phép trước tối thiểu 24 giờ. Đơn nghỉ trên 3 ngày cần xin phép trước 5 ngày làm việc."
                        ),
                        "section_title": "1. Quyền Lợi & Thời Gian Báo Trước",
                    },
                    {
                        "chunk_index": 1,
                        "content": (
                            "## Quy Trình Xin Nghỉ Phép\n"
                            "- Nhân viên gửi yêu cầu qua HR Agent hoặc hệ thống nội bộ.\n"
                            "- HR Agent sẽ kiểm tra quỹ ngày phép còn lại. Nếu hợp lệ, hệ thống tự động chuyển Thẻ Phê Duyệt (Approval Card) tới Quản lý trực tiếp.\n"
                            "- Khi Quản lý bấm Chấp Thuận (Approve), hệ thống sẽ trừ số ngày phép còn lại và ghi nhận vào cơ sở dữ liệu."
                        ),
                        "section_title": "2. Quy Trình Phê Duyệt",
                    },
                ],
            },
            {
                "document_name": "Quy_dinh_Cong_tac_phi_2025.md",
                "department_access": "ALL",
                "chunks": [
                    {
                        "chunk_index": 0,
                        "content": (
                            "# Quy Định Công Tác Phí Doanh Nghiệp\n"
                            "1. Phụ cấp đi lại trong nước: tối đa 500.000 VNĐ / ngày cho chi phí di chuyển nội thành.\n"
                            "2. Chi phí khách sạn: tối đa 1.500.000 VNĐ / đêm đối với cấp Nhân viên và 2.500.000 VNĐ / đêm đối với cấp Quản lý.\n"
                            "3. Hóa đơn chứng từ thanh toán phải có mã số thuế công ty và gửi cho Finance Agent trong vòng 5 ngày làm việc sau chuyến công tác."
                        ),
                        "section_title": "1. Hạn Mức Chi Phí & Chứng Từ",
                    },
                ],
            },
        ]

        for doc_info in sample_docs:
            for chunk_data in doc_info["chunks"]:
                existing = db.query(DocumentChunk).filter(
                    DocumentChunk.tenant_id == tenant.id,
                    DocumentChunk.document_name == doc_info["document_name"],
                    DocumentChunk.chunk_index == chunk_data["chunk_index"],
                ).first()
                if not existing:
                    vec = generate_fallback_embedding(chunk_data["content"])
                    chunk = DocumentChunk(
                        id=uuid.uuid4(),
                        tenant_id=tenant.id,
                        document_name=doc_info["document_name"],
                        department_access=doc_info["department_access"],
                        chunk_index=chunk_data["chunk_index"],
                        content=chunk_data["content"],
                        metadata_={
                            "section_title": chunk_data["section_title"],
                            "document_name": doc_info["document_name"],
                        },
                        dense_embedding=vec,
                    )
                    db.add(chunk)
                    logger.info(f"Seeded DocumentChunk: {doc_info['document_name']} [Chunk {chunk_data['chunk_index']}]")
        db.commit()

        # 5. Seed Additional Employee Users for realistic Dashboard stats
        more_users = [
            {"email": "finance.lead@company.com", "full_name": "Trần Thị Tài Chính", "role": "Manager", "department": "FINANCE"},
            {"email": "legal.counsel@company.com", "full_name": "Nguyễn Văn Pháp Lý", "role": "Manager", "department": "LEGAL"},
            {"email": "it.lead@company.com", "full_name": "Phạm Văn Tech", "role": "Manager", "department": "IT"},
            {"email": "sales.head@company.com", "full_name": "Hoàng Thị Kinh Doanh", "role": "Manager", "department": "SALES"},
        ]
        for udata in more_users:
            user = db.query(User).filter(User.email == udata["email"]).first()
            if not user:
                user = User(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    email=udata["email"],
                    full_name=udata["full_name"],
                    password_hash=get_password_hash(seed_password),
                    role=udata["role"],
                    department=udata["department"],
                )
                db.add(user)
        db.commit()

        employee_user = db.query(User).filter(User.email == "employee@company.com").first()
        it_manager = db.query(User).filter(User.email == "it.lead@company.com").first()
        if employee_user and it_manager and employee_user.manager_id != it_manager.id:
            employee_user.manager_id = it_manager.id
            # Seeded by hand, so a later position assignment must not overwrite it.
            employee_user.manager_is_manual = True
            db.commit()

        # Give the tenant its org tree and place every seeded user in it. Idempotent, so
        # re-running init_db leaves an already-built tree alone.
        backfill_tenant_user_positions(db, tenant.id)
        db.commit()

        # 6. Seed Sample Workflows, Approvals, & Audit Logs across past 7 days
        all_users = db.query(User).filter(User.tenant_id == tenant.id).all()
        employee_u = db.query(User).filter(User.email == "employee@company.com").first() or all_users[0]
        hr_u = db.query(User).filter(User.email == "hr.manager@company.com").first() or all_users[0]

        existing_workflows = db.query(AgentWorkflow).filter(AgentWorkflow.tenant_id == tenant.id).count()
        if existing_workflows == 0:
            sample_wf = AgentWorkflow(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                initiator_id=employee_u.id,
                title="Yêu cầu xin nghỉ phép 2 ngày — Lê Văn Nhẫn",
                status="AWAITING_APPROVAL",
                current_step=1,
                dag_plan={"task": "Nghỉ phép", "days": 2, "reason": "Việc gia đình"},
            )
            db.add(sample_wf)
            db.commit()
            db.refresh(sample_wf)

            sample_approval = WorkflowApproval(
                id=uuid.uuid4(),
                workflow_id=sample_wf.id,
                approver_id=hr_u.id,
                action_type="XIN_NGHI_PHEP",
                risk_level="MEDIUM",
                payload={
                    "requester_name": employee_u.full_name,
                    "days_requested": 2,
                    "remaining_days": 10,
                    "reason": "Việc gia đình",
                },
                status="WAITING",
            )
            db.add(sample_approval)
            db.commit()

        # Seed Audit Logs for last 7 days if count < 10
        audit_count = db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).count()
        if audit_count < 10:
            roles = ["HR", "LEGAL", "IT", "FINANCE", "SALES", "KNOWLEDGE", "CEO"]
            now_dt = datetime.now(timezone.utc)
            for day_offset in range(7):
                created_dt = now_dt - timedelta(days=day_offset)
                for role in roles:
                    log = AuditLog(
                        id=uuid.uuid4(),
                        tenant_id=tenant.id,
                        agent_role=role,
                        tool_name=f"{role.lower()}_assistant_tool",
                        input_parameters={"query": f"Sample execution for {role}"},
                        output_result={"status": "success", "result": f"Executed task for {role}"},
                        execution_time_ms=120 + day_offset * 15,
                        created_at=created_dt,
                    )
                    db.add(log)
            db.commit()
            logger.info("Seeded dynamic sample AuditLogs across past 7 days.")

        logger.info("✅ Database initialization and seeding completed successfully!")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error during database seeding: {e}")
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
