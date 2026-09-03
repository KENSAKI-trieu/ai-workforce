"""Tenant-safe AI Employee configuration and operational statistics."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.hr_capabilities import HR_RETIRED_TOOLS
from app.core.security import RoleRequired, get_current_active_user
from app.models.models import AIAgent, AgentWorkflow, AuditLog, DocumentChunk, LLMCostLog, User
from app.schemas.schemas import AIAgentResponse
from app.services.auth_service import DEFAULT_AGENT_TOOLS, ensure_tenant_default_agents

router = APIRouter(prefix="/agents", tags=["AI Agents"])
AGENT_CONFIG_ROLES = {"Owner", "Admin", "CEO"}
TOOL_DESCRIPTIONS = {
    "hybrid_rag_search": "Tìm và trích dẫn chính sách trong kho tri thức (HR, Legal).",
    "get_employee_private_profile": "Đọc thông tin cá nhân được lọc và masking theo quyền.",
    "get_employee_contract_summary": "Đọc tóm tắt hợp đồng, không trả tài liệu gốc.",
    "get_employee_compensation_summary": "Đọc dữ liệu lương theo quyền và mục đích nghiệp vụ.",
    "get_employee_leave_summary": "Đọc dữ liệu phép trong phạm vi được cấp.",
    "get_employee_full_profile": "Tổng hợp các nhóm hồ sơ được yêu cầu sau khi kiểm tra purpose.",
    "query_company_users_sql": "Chạy truy vấn SQL cố định, có tham số để lấy danh bạ BASIC theo tenant và phạm vi quyền.",
    "query_leave_balance": "Tra cứu quỹ phép cá nhân.",
    "request_leave": "Tạo đơn nghỉ và chuyển phê duyệt.",
    "create_onboarding_workflow": "Khởi tạo workflow onboarding liên phòng ban.",
    "get_contract_expiry": "Theo dõi hợp đồng và thời gian thử việc.",
    "list_pending_hr_approvals": "Liệt kê card chờ duyệt theo phạm vi quản lý.",
    "export_hr_directory": "Xuất danh bạ HR theo quyền ra Excel, PDF hoặc JSON.",
    "generate_and_execute_ceo_dag": "Lập và thực thi kế hoạch đa agent.",
    "hybrid_search_documents": "Tìm kiếm kho tri thức dùng chung (Knowledge). Cùng cơ chế với hybrid_rag_search nhưng là quyền riêng — cấm một tên không cấm tên còn lại.",
    "audit_contract_risk": "Rà soát rủi ro hợp đồng.",
    "compare_contract_versions": "So sánh điều khoản giữa hai phiên bản hợp đồng.",
    "check_sensitive_data": "Phát hiện dữ liệu cá nhân và dữ liệu hạn chế.",
    "check_software_licenses": "Kiểm tra license dependency phần mềm.",
    "generate_legal_document": "Tạo bản nháp văn bản pháp lý DOCX/PDF.",
    "search_it_kb": "Tra cứu tri thức hỗ trợ IT.",
    "create_jira_ticket": "Tạo ticket IT.",
    "reconcile_po_db": "Đối soát hóa đơn và đơn mua hàng.",
    "generate_quotation_pdf": "Tạo báo giá bán hàng.",
}


class AIAgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=4000)
    system_prompt: Optional[str] = Field(None, min_length=10)
    model_name: Optional[str] = Field(None, min_length=2, max_length=100)
    tools_access: Optional[list[str]] = None
    allowed_actions: Optional[list[str]] = None
    disallowed_actions: Optional[list[str]] = None
    knowledge_access: Optional[list[str]] = None
    is_active: Optional[bool] = None


def _get_tenant_agent(db: Session, tenant_id, role_code: str) -> AIAgent:
    agent = db.query(AIAgent).filter(
        AIAgent.tenant_id == tenant_id,
        AIAgent.role_code == role_code.upper(),
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{role_code}' not found")
    return agent


def _public_agent_response(agent: AIAgent, current_user: User) -> AIAgentResponse:
    response = AIAgentResponse.model_validate(agent)
    if current_user.role in AGENT_CONFIG_ROLES:
        return response
    return response.model_copy(update={
        "system_prompt": "Managed by workspace administrators.",
        "tools_access": [],
        "allowed_actions": [],
        "disallowed_actions": [],
        "knowledge_access": [],
    })


def _validate_knowledge_access(db: Session, current_user: User, values: list[str]) -> list[str]:
    selectors = sorted({str(value).strip() for value in values if str(value).strip()})
    if len(selectors) > 5000:
        raise HTTPException(status_code=422, detail="Too many knowledge selectors")
    if "*" in selectors and len(selectors) > 1:
        raise HTTPException(status_code=422, detail="'*' cannot be combined with other knowledge selectors")
    if "none" in selectors and len(selectors) > 1:
        raise HTTPException(status_code=422, detail="'none' cannot be combined with other knowledge selectors")
    if selectors in ([], ["*"], ["none"]):
        return selectors or ["none"]

    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == current_user.tenant_id
    ).all()
    valid_collections = {item.collection_name for item in chunks}
    valid_documents = {item.document_id or item.document_name for item in chunks}
    valid_chunks = {str(item.id) for item in chunks}
    for selector in selectors:
        prefix, separator, value = selector.partition(":")
        if not separator and selector not in valid_collections:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge collection: {selector}")
        if prefix == "collection" and value not in valid_collections:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge collection: {value}")
        if prefix == "document" and value not in valid_documents:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge document: {value}")
        if prefix == "chunk" and value not in valid_chunks:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge chunk: {value}")
        if separator and prefix not in {"collection", "document", "chunk"}:
            raise HTTPException(status_code=422, detail=f"Unsupported knowledge selector: {selector}")
    return selectors


@router.get("/", response_model=List[AIAgentResponse], summary="List tenant AI Employees")
def list_agents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> List[AIAgentResponse]:
    agents = ensure_tenant_default_agents(db, current_user.tenant_id)
    return [_public_agent_response(agent, current_user) for agent in agents]


@router.get("/{role_code}/stats", summary="Get AI Employee history, cost and success rate")
def get_agent_stats(
    role_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    agent = _get_tenant_agent(db, current_user.tenant_id, role_code)
    agent_workflows = [
        workflow
        for workflow in db.query(AgentWorkflow).filter(
            AgentWorkflow.tenant_id == current_user.tenant_id
        ).all()
        if (workflow.dag_plan or {}).get("agent_role") == agent.role_code
    ]
    workflow_total = len(agent_workflows)
    successful = sum(workflow.status == "COMPLETED" for workflow in agent_workflows)
    audit_query = db.query(AuditLog).filter(
        AuditLog.tenant_id == current_user.tenant_id,
        AuditLog.agent_role == agent.role_code,
    )
    total_cost = db.query(func.coalesce(func.sum(LLMCostLog.estimated_cost_usd), 0)).filter(
        LLMCostLog.tenant_id == current_user.tenant_id,
        LLMCostLog.agent_role == agent.role_code,
        LLMCostLog.usage_source.in_(("PROVIDER", "MANUAL_IMPORT")),
    ).scalar()
    history = [
        {
            "id": str(item.id),
            "tool_name": item.tool_name,
            "input_parameters": item.input_parameters,
            "output_result": item.output_result,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in audit_query.order_by(AuditLog.created_at.desc()).limit(20).all()
    ]
    executions = audit_query.count()
    denominator = workflow_total or executions
    success_rate = round(successful / denominator * 100, 1) if denominator else 0.0
    return {
        "role_code": agent.role_code,
        "executions": executions,
        "workflow_total": workflow_total,
        "successful_workflows": successful,
        "success_rate": success_rate,
        "cost_usd": round(float(total_cost or 0), 6),
        "history": history,
    }


@router.get("/{role_code}", response_model=AIAgentResponse, summary="Get an AI Employee")
def get_agent(
    role_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AIAgentResponse:
    return _public_agent_response(
        _get_tenant_agent(db, current_user.tenant_id, role_code), current_user
    )


@router.get(
    "/{role_code}/configuration-options",
    summary="List tools and governed knowledge available to an AI Employee",
    dependencies=[Depends(RoleRequired("Owner", "Admin", "CEO"))],
)
def get_agent_configuration_options(
    role_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    agent = _get_tenant_agent(db, current_user.tenant_id, role_code)
    # A legacy row can still carry a name the executor cannot dispatch. Hide those here as
    # well as revoking them in the migration, so the configuration UI never offers a
    # toggle that does nothing.
    retired = {"get_employee_profile"} | HR_RETIRED_TOOLS
    tool_names = sorted((
        set(DEFAULT_AGENT_TOOLS.get(agent.role_code, []))
        | set(agent.tools_access or [])
        | set(agent.allowed_actions or [])
        | set(agent.disallowed_actions or [])
    ) - retired)
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == current_user.tenant_id
    ).order_by(DocumentChunk.document_name, DocumentChunk.chunk_index).all()
    documents: dict[str, dict] = {}
    for chunk in chunks:
        document_id = chunk.document_id or chunk.document_name
        document = documents.setdefault(document_id, {
            "document_id": document_id,
            "document_name": chunk.document_name,
            "document_title": chunk.document_title or chunk.document_name,
            "collection_name": chunk.collection_name,
            "department_access": chunk.department_access,
            "confidentiality": chunk.confidentiality,
            "status": chunk.status,
            "chunks": [],
        })
        document["chunks"].append({
            "id": str(chunk.id),
            "chunk_index": chunk.chunk_index,
            "section_title": chunk.section_title or f"Chunk {chunk.chunk_index}",
            "page_start": chunk.page_start or chunk.page,
            "page_end": chunk.page_end or chunk.page,
            "status": chunk.status,
            "confidentiality": chunk.confidentiality,
        })
    return {
        "agent_role": agent.role_code,
        "tools": [
            {"name": name, "description": TOOL_DESCRIPTIONS.get(name, name)}
            for name in tool_names
        ],
        "documents": list(documents.values()),
    }


@router.patch(
    "/{role_code}",
    response_model=AIAgentResponse,
    summary="Configure an AI Employee",
    dependencies=[Depends(RoleRequired("Owner", "Admin", "CEO"))],
)
def update_agent(
    role_code: str,
    req: AIAgentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AIAgentResponse:
    agent = _get_tenant_agent(db, current_user.tenant_id, role_code)
    data = req.model_dump(exclude_unset=True)
    if "knowledge_access" in data:
        data["knowledge_access"] = _validate_knowledge_access(
            db, current_user, data["knowledge_access"]
        )
    submitted_tool_names = set().union(*(
        set(data.get(field_name, []))
        for field_name in ("tools_access", "allowed_actions", "disallowed_actions")
        if field_name in data
    ))
    if "get_employee_profile" in submitted_tool_names:
        raise HTTPException(
            status_code=422,
            detail="Deprecated broad HR profile tool is not allowed; select narrow HR tools instead",
        )
    unknown_tools = submitted_tool_names - set(TOOL_DESCRIPTIONS)
    if unknown_tools:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tools: {', '.join(sorted(unknown_tools))}",
        )
    prospective_tools = set(data.get("tools_access", agent.tools_access or []))
    prospective_allowed = set(data.get("allowed_actions", agent.allowed_actions or []))
    prospective_denied = set(data.get("disallowed_actions", agent.disallowed_actions or []))
    overlap = prospective_allowed & prospective_denied
    if overlap:
        raise HTTPException(
            status_code=422,
            detail=f"Actions cannot be both allowed and disallowed: {', '.join(sorted(overlap))}",
        )
    if not prospective_allowed.issubset(prospective_tools):
        raise HTTPException(status_code=422, detail="Allowed actions must also be enabled tools")
    for field_name, value in data.items():
        setattr(agent, field_name, value)
    agent.configuration_version = 5
    db.add(AuditLog(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.id,
        actor_type="USER",
        agent_role=agent.role_code,
        tool_name="configure_ai_employee",
        action="agent.configuration.updated",
        resource_type="AI_AGENT",
        resource_id=str(agent.id),
        input_parameters={"updated_fields": sorted(data)},
        output_result={
            "tools_access": agent.tools_access or [],
            "knowledge_access": agent.knowledge_access or [],
        },
        status="SUCCESS",
        execution_time_ms=0,
    ))
    db.commit()
    db.refresh(agent)
    return AIAgentResponse.model_validate(agent)


@router.patch(
    "/{role_code}/toggle",
    summary="Toggle AI Employee active status",
    dependencies=[Depends(RoleRequired("Owner", "Admin", "CEO"))],
)
def toggle_agent(
    role_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    agent = _get_tenant_agent(db, current_user.tenant_id, role_code)
    agent.is_active = not agent.is_active
    db.commit()
    return {"role_code": agent.role_code, "is_active": agent.is_active}
