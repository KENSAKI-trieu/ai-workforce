"""Workflow definitions and auditable execution runs."""

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.models import AgentWorkflow, User, WorkflowApproval
from app.domains.platform.notification_service import notify_users

router = APIRouter(prefix="/workflows", tags=["Workflow Automation"])
NodeType = Literal[
    "TRIGGER", "CONDITION", "AI_AGENT", "TOOL", "HUMAN_APPROVAL",
    "DELAY", "LOOP", "NOTIFICATION", "OUTPUT",
]
TriggerType = Literal[
    "EMAIL_RECEIVED", "FORM_SUBMITTED", "SCHEDULED", "MANUAL",
    "API_DATA", "TASK_STATUS_CHANGED",
]
ADMIN_ROLES = {"Owner", "Admin", "CEO"}


class WorkflowNode(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    type: NodeType
    name: str = Field(min_length=1, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)
    next: list[str] = Field(default_factory=list)


class WorkflowCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    trigger_type: TriggerType = "MANUAL"
    nodes: list[WorkflowNode] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self):
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Workflow node ids must be unique")
        unknown = {
            next_id
            for node in self.nodes
            for next_id in node.next
            if next_id not in set(node_ids)
        }
        if unknown:
            raise ValueError(f"Unknown next node ids: {', '.join(sorted(unknown))}")
        if not any(node.type == "TRIGGER" for node in self.nodes):
            raise ValueError("Workflow requires a TRIGGER node")
        return self


class WorkflowUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    trigger_type: Optional[TriggerType] = None
    nodes: Optional[list[WorkflowNode]] = None
    is_active: Optional[bool] = None


def _visible_workflow_query(db: Session, current_user: User):
    query = db.query(AgentWorkflow).filter(
        AgentWorkflow.tenant_id == current_user.tenant_id
    )
    if current_user.role in ADMIN_ROLES:
        return query
    if current_user.role == "Manager":
        department_ids = db.query(User.id).filter(
            User.tenant_id == current_user.tenant_id,
            User.department == current_user.department,
        )
        return query.filter(AgentWorkflow.initiator_id.in_(department_ids))
    return query.filter(AgentWorkflow.initiator_id == current_user.id)


def _get_visible_workflow(
    db: Session, current_user: User, workflow_id: uuid.UUID
) -> AgentWorkflow:
    workflow = _visible_workflow_query(db, current_user).filter(
        AgentWorkflow.id == workflow_id
    ).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


def _serialize(workflow: AgentWorkflow) -> dict[str, Any]:
    plan = workflow.dag_plan or {}
    return {
        "id": str(workflow.id),
        "title": workflow.title,
        "description": plan.get("description"),
        "trigger_type": plan.get("trigger_type", "MANUAL"),
        "nodes": plan.get("nodes", []),
        "is_definition": plan.get("kind", "definition") == "definition",
        "definition_id": plan.get("definition_id"),
        "is_active": plan.get("is_active", True),
        "status": workflow.status,
        "current_step": workflow.current_step,
        "initiator_id": str(workflow.initiator_id),
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None,
    }


@router.get("", summary="List workflow definitions")
def list_workflows(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    workflows = _visible_workflow_query(db, current_user).order_by(
        AgentWorkflow.created_at.desc()
    ).all()
    return [
        _serialize(workflow)
        for workflow in workflows
        if (workflow.dag_plan or {}).get("kind", "definition") == "definition"
    ]


@router.post("", status_code=201, summary="Create a workflow definition")
def create_workflow(
    req: WorkflowCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    workflow = AgentWorkflow(
        tenant_id=current_user.tenant_id,
        initiator_id=current_user.id,
        title=req.title,
        status="DRAFT",
        dag_plan={
            "kind": "definition",
            "description": req.description,
            "trigger_type": req.trigger_type,
            "nodes": [node.model_dump() for node in req.nodes],
            "is_active": True,
        },
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    return _serialize(workflow)


@router.get("/{workflow_id}", summary="Get a workflow definition or run")
def get_workflow(
    workflow_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return _serialize(_get_visible_workflow(db, current_user, workflow_id))


@router.patch("/{workflow_id}", summary="Update a workflow definition")
def update_workflow(
    workflow_id: uuid.UUID,
    req: WorkflowUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    workflow = _get_visible_workflow(db, current_user, workflow_id)
    plan = dict(workflow.dag_plan or {})
    if plan.get("kind", "definition") != "definition":
        raise HTTPException(status_code=409, detail="Execution runs are immutable")
    if current_user.role not in ADMIN_ROLES and workflow.initiator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the creator or Admin can edit this workflow")
    data = req.model_dump(exclude_unset=True)
    if "title" in data:
        workflow.title = data.pop("title")
    if "nodes" in data:
        plan["nodes"] = [
            node.model_dump() if isinstance(node, WorkflowNode) else node
            for node in data.pop("nodes")
        ]
    plan.update(data)
    workflow.dag_plan = plan
    db.commit()
    db.refresh(workflow)
    return _serialize(workflow)


@router.post("/{workflow_id}/run", status_code=201, summary="Start a workflow run")
def run_workflow(
    workflow_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    definition = _get_visible_workflow(db, current_user, workflow_id)
    plan = definition.dag_plan or {}
    if plan.get("kind", "definition") != "definition":
        raise HTTPException(status_code=409, detail="Only definitions can be run")
    if not plan.get("is_active", True):
        raise HTTPException(status_code=409, detail="Workflow is inactive")

    nodes = plan.get("nodes", [])
    approval_index = next(
        (index for index, node in enumerate(nodes) if node.get("type") == "HUMAN_APPROVAL"),
        None,
    )
    waiting_for_approval = approval_index is not None
    executed_until = approval_index if waiting_for_approval else len(nodes)
    node_results = [
        {
            "node_id": node.get("id"),
            "type": node.get("type"),
            "status": "COMPLETED" if index < executed_until else "WAITING",
        }
        for index, node in enumerate(nodes)
    ]
    run = AgentWorkflow(
        tenant_id=current_user.tenant_id,
        initiator_id=current_user.id,
        title=f"{definition.title} — run",
        status="AWAITING_APPROVAL" if waiting_for_approval else "COMPLETED",
        current_step=executed_until,
        completed_at=None if waiting_for_approval else datetime.now(timezone.utc),
        thread_id=str(uuid.uuid4()),
        dag_plan={
            "kind": "run",
            "definition_id": str(definition.id),
            "trigger_type": plan.get("trigger_type"),
            "nodes": nodes,
            "node_results": node_results,
        },
    )
    db.add(run)
    db.flush()
    if waiting_for_approval:
        node = nodes[approval_index]
        config = node.get("config", {})
        approval = WorkflowApproval(
            workflow_id=run.id,
            action_type=config.get("action_type", node.get("name", "WORKFLOW_ACTION")),
            risk_level=config.get("risk_level", "MEDIUM"),
            payload=config.get("payload", {
                "reason": "Workflow reached a human approval gate",
                "requester_name": current_user.full_name,
            }),
            status="WAITING",
        )
        db.add(approval)
        db.flush()
        approvers = db.query(User).filter(
            User.tenant_id == current_user.tenant_id,
            User.role.in_(("Owner", "Admin", "CEO", "Manager")),
            User.is_active.is_(True),
        ).all()
        notify_users(
            db,
            approvers,
            event_type="APPROVAL_REQUIRED",
            title="Workflow cần phê duyệt",
            message=run.title,
            severity="WARNING",
            entity_type="APPROVAL",
            entity_id=str(approval.id),
            dedup_key=f"approval:{approval.id}",
        )
    db.commit()
    db.refresh(run)
    return _serialize(run)


@router.get("/{workflow_id}/runs", summary="List execution history for a workflow")
def list_workflow_runs(
    workflow_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _get_visible_workflow(db, current_user, workflow_id)
    runs = _visible_workflow_query(db, current_user).order_by(
        AgentWorkflow.created_at.desc()
    ).all()
    return [
        _serialize(run)
        for run in runs
        if (run.dag_plan or {}).get("definition_id") == str(workflow_id)
    ]
