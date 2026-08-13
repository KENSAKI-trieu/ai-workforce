"""Backend coordinator for durable LangGraph runs and approval interrupts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.core.security import create_internal_tool_token
from app.models.models import (
    AIAgent,
    AgentWorkflow,
    AuditLog,
    ChatConversation,
    ChatMessage,
    User,
    WorkflowApproval,
)
from app.services.ai_service_client import AIServiceClient, get_ai_service_client
from app.services.langgraph_approvals import (
    GRAPH_APPROVAL_KIND,
    GRAPH_WORKFLOW_KIND,
    ensure_graph_approval,
)


class LangGraphEngine:
    """Bind authoritative backend records and identity to AI-service graph calls."""

    def __init__(self, client: AIServiceClient | None = None) -> None:
        self.client = client or get_ai_service_client()

    @staticmethod
    def _payload(
        *,
        user: User,
        agent: AIAgent,
        conversation_id: str,
        workflow_id: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.id),
            "role": user.role,
            "department": user.department,
            "conversation_id": conversation_id,
            "workflow_id": workflow_id,
            "agent_role": agent.role_code,
            "allowed_tools": list(agent.tools_access or []),
            "denied_tools": list(agent.disallowed_actions or []),
        }
        if message is not None:
            payload.update({"message": message, "requested_agent": agent.role_code})
            payload.pop("agent_role")
        return payload

    @staticmethod
    def _workflow(
        db: Session,
        user: User,
        agent: AIAgent,
        conversation_id: str,
        message: str,
    ) -> AgentWorkflow:
        candidates = db.query(AgentWorkflow).filter(
            AgentWorkflow.tenant_id == user.tenant_id,
            AgentWorkflow.initiator_id == user.id,
            AgentWorkflow.thread_id == conversation_id,
        ).all()
        workflow = next(
            (
                item for item in candidates
                if (item.dag_plan or {}).get("kind") == GRAPH_WORKFLOW_KIND
            ),
            None,
        )
        if workflow is None:
            workflow = AgentWorkflow(
                tenant_id=user.tenant_id,
                initiator_id=user.id,
                title=f"AI conversation: {message.strip()[:120]}",
                status="IN_PROGRESS",
                current_step=0,
                thread_id=conversation_id,
                dag_plan={
                    "kind": GRAPH_WORKFLOW_KIND,
                    "conversation_id": conversation_id,
                    "agent_role": agent.role_code,
                },
            )
            db.add(workflow)
            db.flush()
        else:
            workflow.status = "IN_PROGRESS"
            workflow.completed_at = None
            workflow.dag_plan = {
                **(workflow.dag_plan or {}),
                "conversation_id": conversation_id,
                "agent_role": agent.role_code,
            }
        return workflow

    @staticmethod
    def _register_interrupt(
        db: Session,
        *,
        workflow: AgentWorkflow,
        user: User,
        agent: AIAgent,
        interrupt_item: dict[str, Any],
    ) -> WorkflowApproval:
        value = dict(interrupt_item.get("value") or {})
        interrupt_id = str(
            interrupt_item.get("id")
            or value.get("tool_call_id")
            or f"{workflow.thread_id}:{value.get('tool_name')}"
        )
        approval_id = value.get("approval_id")
        if approval_id:
            try:
                approval = db.query(WorkflowApproval).filter(
                    WorkflowApproval.id == uuid.UUID(str(approval_id)),
                    WorkflowApproval.workflow_id == workflow.id,
                    WorkflowApproval.langgraph_interrupt_id == interrupt_id,
                ).first()
            except ValueError:
                approval = None
            if approval is not None:
                return approval
        return ensure_graph_approval(
            db,
            workflow=workflow,
            user=user,
            agent=agent,
            interrupt_id=interrupt_id,
            value=value,
        )

    @staticmethod
    def _sync_result(
        db: Session,
        *,
        result: dict[str, Any],
        workflow: AgentWorkflow,
        user: User,
        agent: AIAgent,
    ) -> WorkflowApproval | None:
        interrupts = result.get("interrupts") or []
        if interrupts:
            approval = LangGraphEngine._register_interrupt(
                db,
                workflow=workflow,
                user=user,
                agent=agent,
                interrupt_item=interrupts[0],
            )
            workflow.status = "AWAITING_APPROVAL"
            workflow.current_step = int(workflow.current_step or 0) + 1
            return approval
        workflow.status = "COMPLETED"
        workflow.completed_at = datetime.now(timezone.utc)
        return None

    def execute(
        self,
        *,
        db: Session,
        user: User,
        agent: AIAgent,
        message: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        workflow = self._workflow(db, user, agent, conversation_id, message)
        db.commit()
        token = create_internal_tool_token(user, agent_role=agent.role_code)
        try:
            result = self.client.run_orchestration(
                self._payload(
                    user=user,
                    agent=agent,
                    conversation_id=conversation_id,
                    workflow_id=str(workflow.id),
                    message=message,
                ),
                internal_tool_jwt=token,
            )
            approval = self._sync_result(
                db,
                result=result,
                workflow=workflow,
                user=user,
                agent=agent,
            )
            self._persist_sanitized_trace(db, workflow, user, agent, result)
            db.commit()
        except Exception:
            db.rollback()
            persisted = db.query(AgentWorkflow).filter(AgentWorkflow.id == workflow.id).first()
            if persisted is not None:
                persisted.status = "RESUME_FAILED"
                db.commit()
            raise
        return self.to_chat_response(result, agent, workflow=workflow, approval=approval)

    def execute_stream(
        self,
        *,
        db: Session,
        user: User,
        agent: AIAgent,
        message: str,
        conversation_id: str,
    ) -> Iterator[dict[str, Any]]:
        """Proxy sanitized SSE events and persist the completed governed run."""
        workflow = self._workflow(db, user, agent, conversation_id, message)
        db.commit()
        token = create_internal_tool_token(user, agent_role=agent.role_code)
        result: dict[str, Any] | None = None
        try:
            for item in self.client.stream_orchestration(
                self._payload(
                    user=user,
                    agent=agent,
                    conversation_id=conversation_id,
                    workflow_id=str(workflow.id),
                    message=message,
                ),
                internal_tool_jwt=token,
            ):
                event = item.get("event")
                if event in {"status", "token"}:
                    yield item
                elif event == "error":
                    raise AIServiceError("AI orchestration stream failed")
                elif event == "result":
                    result = {key: value for key, value in item.items() if key != "event"}

            if result is None:
                raise AIServiceError("AI orchestration stream ended without a result")
            approval = self._sync_result(
                db,
                result=result,
                workflow=workflow,
                user=user,
                agent=agent,
            )
            self._persist_sanitized_trace(db, workflow, user, agent, result)
            db.commit()
        except Exception:
            db.rollback()
            persisted = db.query(AgentWorkflow).filter(AgentWorkflow.id == workflow.id).first()
            if persisted is not None:
                persisted.status = "RESUME_FAILED"
                db.commit()
            raise
        yield {
            "event": "complete",
            "response": self.to_chat_response(
                result, agent, workflow=workflow, approval=approval
            ),
        }

    @staticmethod
    def sanitize_execution_trace(result: dict[str, Any]) -> list[dict[str, Any]]:
        """Reduce graph internals to an allow-listed, audit-safe business trace."""
        phase_by_node = {
            "input_guard": "ANALYZING",
            "intent_router": "ANALYZING",
            "agent_selector": "ANALYZING",
            "legal_policy": "ANALYZING",
            "legal_tool_scope": "ANALYZING",
            "hr_policy": "ANALYZING",
            "hr_tool_scope": "ANALYZING",
            "finance_policy": "ANALYZING",
            "finance_tool_scope": "ANALYZING",
            "customer_support_policy": "ANALYZING",
            "customer_support_tool_scope": "ANALYZING",
            "knowledge_policy": "ANALYZING",
            "knowledge_tool_scope": "ANALYZING",
            "ceo_policy": "ANALYZING",
            "ceo_tool_scope": "ANALYZING",
            "retrieve_context": "SEARCHING",
            "model_decision": "ANALYZING",
            "execute_read_tool": "TOOL_CALLING",
            "approval_interrupt": "WAITING_APPROVAL",
            "output_validation": "ANALYZING",
            "citation_verification": "ANALYZING",
            "response": "COMPLETED",
        }
        trace: list[dict[str, Any]] = []
        for item in (result.get("state") or {}).get("execution_trace") or []:
            phase = phase_by_node.get(str(item.get("node") or ""))
            if not phase:
                continue
            raw_status = str(item.get("status") or "COMPLETED").upper()
            public_status = "FAILED" if raw_status in {"FAILED", "DENIED", "LIMITED"} else "COMPLETED"
            candidate = {"phase": phase, "status": public_status}
            if not trace or trace[-1] != candidate:
                trace.append(candidate)
        if result.get("status") == "AWAITING_APPROVAL":
            candidate = {"phase": "WAITING_APPROVAL", "status": "PENDING"}
            if not trace or trace[-1] != candidate:
                trace.append(candidate)
        elif not trace or trace[-1].get("phase") != "COMPLETED":
            trace.append({"phase": "COMPLETED", "status": "COMPLETED"})
        return [{"sequence": index + 1, **item} for index, item in enumerate(trace)]

    @staticmethod
    def sanitize_tool_calls(state: dict[str, Any]) -> list[dict[str, Any]]:
        """Expose tool outcomes without model rationale, arguments or raw results."""
        return [
            {
                "tool_name": str(item.get("name") or item.get("tool_name") or "tool")[:100],
                "action": str(item.get("action") or "READ_ONLY")[:30],
                "status": str(item.get("status") or "COMPLETED")[:30],
            }
            for item in (state.get("tool_calls") or [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _persist_sanitized_trace(
        db: Session,
        workflow: AgentWorkflow,
        user: User,
        agent: AIAgent,
        result: dict[str, Any],
    ) -> None:
        trace = LangGraphEngine.sanitize_execution_trace(result)
        workflow.dag_plan = {**(workflow.dag_plan or {}), "execution_trace": trace}
        db.add(AuditLog(
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            actor_type="AGENT",
            workflow_id=workflow.id,
            agent_role=agent.role_code,
            tool_name="langgraph_orchestration",
            action="orchestration.execution_trace",
            resource_type="AGENT_WORKFLOW",
            resource_id=str(workflow.id),
            input_parameters=None,
            output_result={"status": result.get("status"), "trace": trace},
            status="PENDING" if result.get("status") == "AWAITING_APPROVAL" else "SUCCESS",
            execution_time_ms=0,
        ))

    def resume_approval(
        self,
        *,
        db: Session,
        approval: WorkflowApproval,
        approved: bool,
        reviewer: User,
        comments: str | None,
    ) -> dict[str, Any]:
        workflow = approval.workflow
        payload = approval.payload or {}
        if payload.get("kind") != GRAPH_APPROVAL_KIND:
            raise ValueError("Approval is not linked to a LangGraph interrupt")
        user = workflow.initiator
        agent_role = str(payload.get("agent_role") or (workflow.dag_plan or {}).get("agent_role") or "")
        agent = db.query(AIAgent).filter(
            AIAgent.tenant_id == workflow.tenant_id,
            AIAgent.role_code == agent_role,
            AIAgent.is_active.is_(True),
        ).first()
        if agent is None:
            raise ValueError("The AI Employee for this checkpoint is unavailable")
        token = create_internal_tool_token(user, agent_role=agent.role_code)
        result = self.client.resume_orchestration(
            self._payload(
                user=user,
                agent=agent,
                conversation_id=str(workflow.thread_id),
                workflow_id=str(workflow.id),
            ) | {
                "resume": {
                    "approved": approved,
                    "approval_id": str(approval.id),
                    "reviewer_id": str(reviewer.id),
                    "comments": comments,
                }
            },
            internal_tool_jwt=token,
        )
        next_approval = self._sync_result(
            db,
            result=result,
            workflow=workflow,
            user=user,
            agent=agent,
        )
        self._persist_sanitized_trace(db, workflow, user, agent, result)
        if not approved and result.get("status") == "COMPLETED":
            workflow.status = "FAILED"
            workflow.completed_at = datetime.now(timezone.utc)
        approval.resumed_at = datetime.now(timezone.utc)
        approval.resume_error = None
        self._persist_resumed_message(db, workflow, result, approval)
        db.commit()
        return self.to_chat_response(
            result,
            agent,
            workflow=workflow,
            approval=next_approval,
        )

    @staticmethod
    def _persist_resumed_message(
        db: Session,
        workflow: AgentWorkflow,
        result: dict[str, Any],
        approval: WorkflowApproval,
    ) -> None:
        state = result.get("state") or {}
        answer = str(state.get("final_answer") or "").strip()
        if not answer or result.get("status") != "COMPLETED":
            return
        try:
            conversation_id = uuid.UUID(str(workflow.thread_id))
        except ValueError:
            return
        conversation = db.query(ChatConversation).filter(
            ChatConversation.id == conversation_id,
            ChatConversation.tenant_id == workflow.tenant_id,
            ChatConversation.user_id == workflow.initiator_id,
        ).first()
        if conversation is None:
            return
        db.add(ChatMessage(
            conversation_id=conversation.id,
            sender="ASSISTANT",
            content=answer,
            citations=state.get("citations") or [],
            tools_executed=LangGraphEngine.sanitize_tool_calls(state),
            attachments=[{
                "type": "LANGGRAPH_RESUME",
                "payload": {
                    "approval_id": str(approval.id),
                    "workflow_id": str(workflow.id),
                },
            }],
        ))
        conversation.updated_at = datetime.now(timezone.utc)

    @staticmethod
    def approval_card(approval: WorkflowApproval) -> dict[str, Any]:
        payload = approval.payload or {}
        return {
            "id": str(approval.id),
            "workflow_id": str(approval.workflow_id),
            "action_type": approval.action_type,
            "requester_name": payload.get("requester_name"),
            "details": payload.get("reason"),
            "tool_name": payload.get("tool_name"),
            "arguments": payload.get("arguments") or {},
            "risk_level": approval.risk_level,
            "status": approval.status,
        }

    @staticmethod
    def to_chat_response(
        result: dict[str, Any],
        agent: AIAgent,
        *,
        workflow: AgentWorkflow,
        approval: WorkflowApproval | None,
    ) -> dict[str, Any]:
        state = result.get("state") or {}
        approval_card = LangGraphEngine.approval_card(approval) if approval else None
        return {
            "agent_name": agent.name,
            "agent_role": state.get("selected_agent") or agent.role_code,
            "avatar_emoji": agent.avatar_emoji,
            "reply": state.get("final_answer") or (
                "This action is waiting for human approval." if approval_card else ""
            ),
            "citations": state.get("citations") or [],
            "tools_executed": LangGraphEngine.sanitize_tool_calls(state),
            "approval_card": approval_card,
            "hr_card": None,
            "jira_card": None,
            "legal_risk_card": None,
            "invoice_card": None,
            "quote_card": None,
            "dag_plan_card": None,
            "orchestration": {
                "workflow_id": str(workflow.id),
                "thread_id": result.get("thread_id"),
                "status": result.get("status"),
                "errors": [
                    {"code": str(item.get("error") or "ORCHESTRATION_ERROR")[:100]}
                    for item in (state.get("errors") or [])
                    if isinstance(item, dict)
                ],
                "execution_trace": LangGraphEngine.sanitize_execution_trace(result),
            },
        }
