"""Tenant-safe AI Employee configuration and operational statistics."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.gateway_tools import GATEWAY_TOOL_DESCRIPTIONS
from app.core.hr_capabilities import HR_CONFIGURATION_VERSION, HR_RETIRED_TOOLS
from app.core.tool_permissions import canonical_tool_names
from app.core.security import RoleRequired, get_current_active_user
from app.models.models import AIAgent, AgentWorkflow, AuditLog, DocumentChunk, LLMCostLog, User
from app.schemas.schemas import AIAgentResponse
from app.services.agent_knowledge_scope import existing_knowledge_targets, orphaned_selectors
from app.services.auth_service import ensure_tenant_default_agents, supported_agent_tools

router = APIRouter(prefix="/agents", tags=["AI Agents"])
AGENT_CONFIG_ROLES = {"Owner", "Admin", "CEO"}
TOOL_DESCRIPTIONS = {
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
    "audit_contract_risk": "Rà soát rủi ro hợp đồng.",
    "compare_contract_versions": "So sánh điều khoản giữa hai phiên bản hợp đồng.",
    "check_sensitive_data": "Phát hiện dữ liệu cá nhân và dữ liệu hạn chế.",
    "check_software_licenses": "Kiểm tra license dependency phần mềm.",
    "generate_legal_document": "Tạo bản nháp văn bản pháp lý DOCX/PDF.",
    "search_it_kb": "Tra cứu tri thức hỗ trợ IT.",
    "create_jira_ticket": "Tạo ticket IT.",
    "reconcile_po_db": "Đối soát hóa đơn và đơn mua hàng.",
    "generate_quotation_pdf": "Tạo báo giá bán hàng.",
    # Gateway tool names share this column with the capability names above. Leaving them
    # out made every seeded non-HR agent unsaveable: the UI submits the grants it was
    # given, and the unknown-tool check below rejected the ones it had never heard of.
    **GATEWAY_TOOL_DESCRIPTIONS,
}


class AIAgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=4000)
    # `system_prompt` is intentionally absent. It was accepted and stored here for a
    # long time while no part of the runtime ever read it, so every edit made through
    # this field silently did nothing. `prompt_overlay` replaces it and is applied for
    # real; the old column is left untouched rather than migrated, because its stored
    # values are a mix of seed text and abandoned edits.
    prompt_overlay: Optional[str] = Field(None, max_length=8000)
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
        "prompt_overlay": None,
        "tools_access": [],
        "allowed_actions": [],
        "disallowed_actions": [],
        "knowledge_access": [],
    })


def _validate_knowledge_access(
    db: Session,
    current_user: User,
    values: list[str],
    already_granted: frozenset[str] = frozenset(),
) -> list[str]:
    """Reject selectors naming knowledge that does not exist -- unless already granted.

    A selector the agent already holds may point at a document deleted since; refusing it
    made the agent unsaveable, because the page cannot show it for the operator to remove.
    It is kept as it is (it matches nothing), and only newly added selectors must exist.
    """
    selectors = sorted({str(value).strip() for value in values if str(value).strip()})
    if len(selectors) > 5000:
        raise HTTPException(status_code=422, detail="Too many knowledge selectors")
    if "*" in selectors and len(selectors) > 1:
        raise HTTPException(status_code=422, detail="'*' cannot be combined with other knowledge selectors")
    if "none" in selectors and len(selectors) > 1:
        raise HTTPException(status_code=422, detail="'none' cannot be combined with other knowledge selectors")
    if selectors in ([], ["*"], ["none"]):
        return selectors or ["none"]

    valid_collections, valid_documents, valid_chunks = existing_knowledge_targets(
        db, current_user.tenant_id
    )
    for selector in selectors:
        prefix, separator, value = selector.partition(":")
        if separator and prefix not in {"collection", "document", "chunk"}:
            raise HTTPException(status_code=422, detail=f"Unsupported knowledge selector: {selector}")
        if selector in already_granted:
            continue
        if not separator and selector not in valid_collections:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge collection: {selector}")
        if prefix == "collection" and value not in valid_collections:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge collection: {value}")
        if prefix == "document" and value not in valid_documents:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge document: {value}")
        if prefix == "chunk" and value not in valid_chunks:
            raise HTTPException(status_code=422, detail=f"Unknown knowledge chunk: {value}")
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
    # Only tools that change what this role does. Offering every granted name put toggles
    # such as `request_leave` on the Legal agent, which no Legal branch ever checks.
    supported = supported_agent_tools(agent.role_code)
    tool_names = sorted(supported - retired)
    granted = (
        set(canonical_tool_names(agent.tools_access))
        | set(canonical_tool_names(agent.allowed_actions))
        | set(canonical_tool_names(agent.disallowed_actions))
    )
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
        # Grants this role cannot use. The page drops them on the next save.
        "unsupported_grants": sorted(granted - supported - retired),
        # Selectors the agent holds for knowledge deleted since. The page lists them so the
        # operator can see and remove them; they match nothing at retrieval time.
        "orphaned_knowledge": orphaned_selectors(
            agent.knowledge_access or [],
            existing_knowledge_targets(db, current_user.tenant_id),
        ),
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
            db,
            current_user,
            data["knowledge_access"],
            already_granted=frozenset(agent.knowledge_access or []),
        )
    # Stored in the current spelling; a client still sending a renamed tool keeps working.
    for field_name in ("tools_access", "allowed_actions", "disallowed_actions"):
        if field_name in data:
            data[field_name] = canonical_tool_names(data[field_name])
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
    # Grants the agent already holds are let through so an older configuration can still
    # be saved; only newly added tools must be ones this role can use.
    already_granted = (
        set(canonical_tool_names(agent.tools_access))
        | set(canonical_tool_names(agent.allowed_actions))
        | set(canonical_tool_names(agent.disallowed_actions))
    )
    unsupported_tools = (
        submitted_tool_names - already_granted - supported_agent_tools(agent.role_code)
    )
    if unsupported_tools:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tools not used by the {agent.role_code} agent: "
                f"{', '.join(sorted(unsupported_tools))}"
            ),
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
    # Stamping the current version, not a literal: a lower number makes the seeding
    # migration treat this row as legacy and re-add defaults the operator just removed.
    agent.configuration_version = HR_CONFIGURATION_VERSION
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
