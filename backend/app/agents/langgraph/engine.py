"""Backend coordinator for durable LangGraph runs and approval interrupts."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.core.gateway_tools import (
    GATEWAY_TOOL_LABELS,
    disabled_gateway_tools,
    effective_tool_grants,
)
from app.core.security import create_internal_tool_token
from app.domains.finance.visuals import charts_from_tool_calls
from app.models.models import (
    AIAgent,
    AgentWorkflow,
    AuditLog,
    ChatConversation,
    ChatMessage,
    ContractReview,
    User,
    WorkflowApproval,
)
from app.plugins.resolver import resolve_skill_restriction, tenant_graph_instructions
from app.clients.ai_service_client import (
    AIServiceClient,
    AIServiceError,
    get_ai_service_client,
)
from app.agents.langgraph.approvals import (
    GRAPH_APPROVAL_KIND,
    GRAPH_WORKFLOW_KIND,
    ensure_graph_approval,
)
from app.agents.legal.review import _legal_review_card, _load_legal_review_draft


# How much of the conversation the graph sees besides the current message. The graph used
# to receive the current message alone, so a follow-up ("còn phòng IT thì sao?") lost the
# question it followed. Newest turns win when the budget runs out; each message is capped
# so one pasted contract cannot crowd out every other turn.
HISTORY_MESSAGES = 10
HISTORY_MESSAGE_CHARS = 1500
HISTORY_TOTAL_CHARS = 6000


class GraphModelUnavailable(AIServiceError):
    """The graph's decision model failed before any tool ran, so the turn produced nothing.

    Raised as a 503 so the chat falls back to the deterministic flow exactly as it does
    when the AI service itself is down. A provider outage (Gemini answered "503 high
    demand" three times in a row during testing) used to reach the user as the graph's
    English failure notice.
    """

    def __init__(self) -> None:
        super().__init__("The graph's decision model is unavailable", status_code=503)


def role_restricted_tools(user: User, allowed_tools: list[str]) -> list[dict[str, str]]:
    """The agent's granted gateway tools whose role/department ACL refuses this user."""
    # Imported here: the registry builds itself at import time and pulls in the chat flows.
    from app.tools.registry import tool_registry

    definitions = {definition.name: definition for definition in tool_registry.all()}
    return [
        {"name": name, "label": GATEWAY_TOOL_LABELS[name]}
        for name in sorted(set(allowed_tools) & set(GATEWAY_TOOL_LABELS))
        if name in definitions and not definitions[name].acl.permits(user)
    ]


def model_unavailable(result: dict[str, Any]) -> bool:
    state = result.get("state") or {}
    if result.get("status") == "AWAITING_APPROVAL" or state.get("tool_calls"):
        # A tool already ran: falling back would repeat its effects.
        return False
    return any(
        isinstance(item, dict) and item.get("node") == "model_decision"
        for item in state.get("errors") or []
    )


SUSPENDED_CONVERSATION_REPLY = (
    "Cuộc hội thoại này đang chờ phê duyệt cho một hành động trước đó, nên tôi chưa xử lý "
    "tin nhắn mới ở đây. Tôi sẽ tiếp tục khi yêu cầu được duyệt hoặc từ chối. Nếu cần hỏi "
    "việc khác, bạn hãy mở một cuộc hội thoại mới."
)

# The tag retrieval hands the model: "[Citation: <document>, v<version>, <section>; chunk=<id>]".
CITATION_TAG = re.compile(r"\[Citation:\s*([^\]]+)\]", re.IGNORECASE)
CHUNK_REFERENCE = re.compile(r";?\s*chunk\s*=\s*([^\s;,\]]+)", re.IGNORECASE)


class LangGraphEngine:
    """Bind authoritative backend records and identity to AI-service graph calls."""

    def __init__(self, client: AIServiceClient | None = None) -> None:
        self.client = client or get_ai_service_client()

    @staticmethod
    def _payload(
        *,
        db: Session,
        user: User,
        agent: AIAgent,
        conversation_id: str,
        workflow_id: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        restriction = resolve_skill_restriction(db, user.tenant_id, agent.role_code)
        # The effective grant, not the raw column: `allowed_actions` narrows
        # `tools_access`, and a tenant's plugins can narrow it further. The gateway
        # refuses anything outside it, so offering the model more only costs a turn.
        allowed_tools = [
            tool
            for tool in effective_tool_grants(
                agent.tools_access, agent.allowed_actions, agent.disallowed_actions
            )
            if restriction.permits(tool)
        ]
        payload = {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.id),
            "role": user.role,
            "department": user.department,
            "conversation_id": conversation_id,
            "workflow_id": workflow_id,
            "agent_role": agent.role_code,
            "allowed_tools": allowed_tools,
            "denied_tools": list(agent.disallowed_actions or []),
            # Named to the model so a request needing one is told the feature is off; a
            # model that only saw the tool missing answered the task itself, and the user
            # got "citations could not be verified" instead.
            "disabled_tools": disabled_gateway_tools(
                agent.role_code, agent.tools_access, allowed_tools
            ),
            # Granted, but the gateway's ACL refuses them to this user, so they never reach
            # the model's tool list. Asked for an NDA without the drafting tool, the model
            # explained the gap with a company rule the documents never state.
            "restricted_tools": role_restricted_tools(user, allowed_tools),
            # What the tenant added to this agent's reply prompt -- plugin appends and the
            # administrator's own text. The graph puts it after its own rules.
            "tenant_instructions": tenant_graph_instructions(db, user.tenant_id, agent.role_code),
        }
        if message is not None:
            payload.update({
                "message": message,
                "requested_agent": agent.role_code,
                "history": LangGraphEngine._history(db, conversation_id, message),
            })
            payload.pop("agent_role")
        return payload

    @staticmethod
    def _history(db: Session, conversation_id: str, current_message: str) -> list[dict[str, str]]:
        """The turns before this one, oldest first, within the history budget.

        The chat API stores the user's message before the engine runs, so the newest USER
        row is this turn and is left out -- it travels as `message`.
        """
        try:
            conversation_uuid = uuid.UUID(str(conversation_id))
        except ValueError:
            return []
        rows = db.query(ChatMessage).filter(
            ChatMessage.conversation_id == conversation_uuid,
            ChatMessage.sender.in_(("USER", "ASSISTANT")),
        ).order_by(ChatMessage.created_at.desc()).limit(HISTORY_MESSAGES + 1).all()
        if rows and rows[0].sender == "USER" and (rows[0].content or "").strip() == current_message.strip():
            rows = rows[1:]
        history: list[dict[str, str]] = []
        budget = HISTORY_TOTAL_CHARS
        for row in rows[:HISTORY_MESSAGES]:
            text = (row.content or "").strip()
            if not text:
                continue
            if len(text) > HISTORY_MESSAGE_CHARS:
                text = text[:HISTORY_MESSAGE_CHARS].rstrip() + " […]"
            if len(text) > budget:
                break
            budget -= len(text)
            history.append({"role": "user" if row.sender == "USER" else "assistant", "content": text})
        return list(reversed(history))

    @staticmethod
    def _conversation_workflow(
        db: Session, user: User, conversation_id: str
    ) -> AgentWorkflow | None:
        candidates = db.query(AgentWorkflow).filter(
            AgentWorkflow.tenant_id == user.tenant_id,
            AgentWorkflow.initiator_id == user.id,
            AgentWorkflow.thread_id == conversation_id,
        ).all()
        return next(
            (
                item for item in candidates
                if (item.dag_plan or {}).get("kind") == GRAPH_WORKFLOW_KIND
            ),
            None,
        )

    @staticmethod
    def _pending_graph_approval(
        db: Session, user: User, conversation_id: str
    ) -> tuple[AgentWorkflow, WorkflowApproval] | None:
        """The approval this conversation's graph is suspended on, if any.

        A suspended graph refuses a new run until it is resumed. Sending one anyway came
        back as a 422, surfaced to the user as a 500 -- and on the way the conversation's
        workflow was flipped from AWAITING_APPROVAL to IN_PROGRESS and then RESUME_FAILED,
        corrupting the state the pending approval's resume relies on.
        """
        workflow = LangGraphEngine._conversation_workflow(db, user, conversation_id)
        if workflow is None:
            return None
        approval = next(
            (
                item
                for item in db.query(WorkflowApproval).filter(
                    WorkflowApproval.workflow_id == workflow.id,
                    WorkflowApproval.status == "WAITING",
                ).all()
                if (item.payload or {}).get("kind") == GRAPH_APPROVAL_KIND
            ),
            None,
        )
        return (workflow, approval) if approval is not None else None

    def _suspended_response(
        self,
        agent: AIAgent,
        workflow: AgentWorkflow,
        approval: WorkflowApproval,
        conversation_id: str,
    ) -> dict[str, Any]:
        return self.to_chat_response(
            {
                "thread_id": conversation_id,
                "status": "AWAITING_APPROVAL",
                "state": {"final_answer": SUSPENDED_CONVERSATION_REPLY},
            },
            agent,
            workflow=workflow,
            approval=approval,
        )

    @staticmethod
    def _workflow(
        db: Session,
        user: User,
        agent: AIAgent,
        conversation_id: str,
        message: str,
    ) -> AgentWorkflow:
        workflow = LangGraphEngine._conversation_workflow(db, user, conversation_id)
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
        # The tool call id comes first: it is the key the graph registered this approval
        # under through the gateway before suspending. LangGraph's own interrupt id is a
        # different value, and keying on it opened a second, identical approval.
        interrupt_id = str(
            value.get("tool_call_id")
            or interrupt_item.get("id")
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
        suspended = self._pending_graph_approval(db, user, conversation_id)
        if suspended is not None:
            return self._suspended_response(agent, *suspended, conversation_id)
        workflow = self._workflow(db, user, agent, conversation_id, message)
        db.commit()
        token = create_internal_tool_token(user, agent_role=agent.role_code)
        try:
            result = self.client.run_orchestration(
                self._payload(
                    db=db,
                    user=user,
                    agent=agent,
                    conversation_id=conversation_id,
                    workflow_id=str(workflow.id),
                    message=message,
                ),
                internal_tool_jwt=token,
            )
            if model_unavailable(result):
                raise GraphModelUnavailable()
            approval = self._sync_result(
                db,
                result=result,
                workflow=workflow,
                user=user,
                agent=agent,
            )
            self._persist_sanitized_trace(db, workflow, user, agent, result)
            db.commit()
        except Exception as exc:
            db.rollback()
            persisted = db.query(AgentWorkflow).filter(AgentWorkflow.id == workflow.id).first()
            if persisted is not None:
                persisted.status = "FAILED" if isinstance(exc, GraphModelUnavailable) else "RESUME_FAILED"
                db.commit()
            raise
        return self.to_chat_response(
            result,
            agent,
            workflow=workflow,
            approval=approval,
            legal_risk_card=self.legal_risk_card(
                db, user, result, agent=agent, conversation_id=conversation_id
            ),
        )

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
        suspended = self._pending_graph_approval(db, user, conversation_id)
        if suspended is not None:
            yield {
                "event": "complete",
                "response": self._suspended_response(agent, *suspended, conversation_id),
            }
            return
        workflow = self._workflow(db, user, agent, conversation_id, message)
        db.commit()
        token = create_internal_tool_token(user, agent_role=agent.role_code)
        result: dict[str, Any] | None = None
        # Held until the result says the turn succeeded: a failed turn falls back to the
        # deterministic flow, and its tokens must not reach the user first.
        tokens: list[dict[str, Any]] = []
        try:
            for item in self.client.stream_orchestration(
                self._payload(
                    db=db,
                    user=user,
                    agent=agent,
                    conversation_id=conversation_id,
                    workflow_id=str(workflow.id),
                    message=message,
                ),
                internal_tool_jwt=token,
            ):
                event = item.get("event")
                if event == "status":
                    yield item
                elif event == "token":
                    tokens.append(item)
                elif event == "error":
                    raise AIServiceError("AI orchestration stream failed")
                elif event == "result":
                    result = {key: value for key, value in item.items() if key != "event"}

            if result is None:
                raise AIServiceError("AI orchestration stream ended without a result")
            if model_unavailable(result):
                raise GraphModelUnavailable()
            approval = self._sync_result(
                db,
                result=result,
                workflow=workflow,
                user=user,
                agent=agent,
            )
            self._persist_sanitized_trace(db, workflow, user, agent, result)
            db.commit()
        except Exception as exc:
            db.rollback()
            persisted = db.query(AgentWorkflow).filter(AgentWorkflow.id == workflow.id).first()
            if persisted is not None:
                persisted.status = "FAILED" if isinstance(exc, GraphModelUnavailable) else "RESUME_FAILED"
                db.commit()
            raise
        yield from tokens
        yield {
            "event": "complete",
            "response": self.to_chat_response(
                result,
                agent,
                workflow=workflow,
                approval=approval,
                legal_risk_card=self.legal_risk_card(
                    db, user, result, agent=agent, conversation_id=conversation_id
                ),
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
                db=db,
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
            legal_risk_card=self.legal_risk_card(db, user, result),
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
    def legal_risk_card(
        db: Session,
        user: User | None,
        result: dict[str, Any],
        *,
        agent: AIAgent | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """This turn's contract review card, in the shape the deterministic Legal chat uses.

        A finished review is the saved review behind it: the model only receives a summary,
        so without this the user would get a paragraph and no card -- no findings, no
        evidence, no redline. A review still waiting for the user's side is the same open
        question the deterministic chat leaves, so a next turn that falls back to that chat
        still reads "bên B" as the answer. Any other Legal turn closes a question left open
        earlier: the user moved on, and a later message must not be read as answering it.
        `tool_calls` starts empty on every run, so each entry belongs to this turn.
        """
        if user is None:
            return None
        calls = (result.get("state") or {}).get("tool_calls") or []
        for call in reversed(calls):
            if not isinstance(call, dict) or call.get("name") != "audit_contract_risk":
                continue
            if call.get("status") != "SUCCESS" or not isinstance(call.get("result"), dict):
                break
            outcome = call["result"]
            if outcome.get("status") == "NEEDS_REPRESENTED_PARTY" and outcome.get("contract_fingerprint"):
                return _legal_review_card(
                    status="COLLECTING",
                    contract_fingerprint=str(outcome["contract_fingerprint"]),
                    contract_char_count=int(outcome.get("contract_char_count") or 0),
                    excerpt=str(outcome.get("contract_excerpt") or ""),
                    document_scope=outcome.get("document_scope"),
                )
            review_id = outcome.get("review_id")
            if not review_id:
                # Nothing was reviewed: no text, or one that could not be translated.
                break
            try:
                review_uuid = uuid.UUID(str(review_id))
            except ValueError:
                return None
            # The id came back through the model's run, so it is only trusted as far as
            # it names a review this user made in this tenant.
            review = db.query(ContractReview).filter(
                ContractReview.id == review_uuid,
                ContractReview.tenant_id == user.tenant_id,
                ContractReview.created_by_id == user.id,
            ).first()
            if review is None:
                return None
            return {
                **(review.result or {}),
                "review_id": str(review.id),
                "redline_url": f"/api/v1/legal/contract-reviews/{review.id}/redline",
            }
        if agent is None or agent.role_code.upper() != "LEGAL" or not conversation_id:
            return None
        pending = _load_legal_review_draft(db, user, conversation_id)
        if pending is None:
            return None
        draft, _ = pending
        return _legal_review_card(
            status="DISMISSED",
            contract_fingerprint=str(draft.get("contract_fingerprint") or ""),
            contract_char_count=int(draft.get("contract_char_count") or 0),
        )

    @staticmethod
    def public_citations(state: dict[str, Any]) -> list[dict[str, Any]]:
        """The retrieved chunks the answer cites, in the shape the chat shows.

        The model writes its own citation objects, and their keys vary from turn to turn
        (`chunk_id` one time, `source` or a pasted tag the next), so the chat showed
        "Tài liệu nội bộ" instead of the document. Each citation is matched back to the
        retrieved chunk it names and replaced by that chunk's own metadata; one that
        matches nothing retrieved is dropped (the graph has already verified the answer).
        Retrieved means what the graph verifies against: its own search and every search
        the model ran after it -- matched against the first alone, an answer resting on a
        later search reached the user verified but with no sources.
        """
        context = [item for item in (state.get("retrieved_context") or []) if isinstance(item, dict)]
        for call in state.get("tool_calls") or []:
            if (
                isinstance(call, dict)
                and call.get("name") == "rag_search"
                and call.get("status") == "SUCCESS"
                and isinstance(call.get("result"), list)
            ):
                context.extend(item for item in call["result"] if isinstance(item, dict))
        references: list[set[str]] = [
            {
                str(value).strip().casefold()
                for field in ("chunk_id", "id", "document_id", "document_title", "document_name", "source", "citation_tag")
                for value in [citation.get(field)]
                if value
            }
            for citation in state.get("citations") or []
            if isinstance(citation, dict)
        ]
        # The tags the answer itself carries: the model's citation objects sometimes hold
        # only part of one (a document and section, no chunk), and the answer the graph
        # verified was shown with no sources.
        references.extend({tag.strip().casefold()} for tag in CITATION_TAG.findall(state.get("final_answer") or ""))

        def chunk_match(keys: set[str]) -> dict[str, Any] | None:
            ids = keys | {match.group(1) for key in keys for match in [CHUNK_REFERENCE.search(key)] if match}
            return next((item for item in context if str(item.get("id") or "").casefold() in ids), None)

        def named_documents(keys: set[str]) -> set[str]:
            # As the graph verifies a tag: its document is what comes before the first comma.
            return keys | {CHUNK_REFERENCE.sub("", key).split(",", 1)[0].strip() for key in keys}

        def document_fields(item: dict[str, Any]) -> set[str]:
            return {
                str(item.get(field) or "").casefold()
                for field in ("document_id", "document_title", "document_name")
            } - {""}

        cited: list[dict[str, Any]] = []

        def cite(match: dict[str, Any]) -> None:
            if any(item.get("id") == match.get("id") for item in cited):
                return
            cited.append({
                field: match.get(field)
                for field in ("id", "document_id", "document_name", "document_title", "section_title", "version", "citation_tag")
                if match.get(field) is not None
            })

        # A reference naming its chunk shows that chunk. One naming only a document shows
        # that document's first retrieved chunk -- unless a chunk of it is already shown.
        unmatched: list[set[str]] = []
        for keys in references:
            match = chunk_match(keys)
            if match is None:
                unmatched.append(keys)
            else:
                cite(match)
        for keys in unmatched:
            documents = named_documents(keys)
            if any(document_fields(item) & documents for item in cited):
                continue
            match = next((item for item in context if document_fields(item) & documents), None)
            if match is not None:
                cite(match)
        return cited

    @staticmethod
    def to_chat_response(
        result: dict[str, Any],
        agent: AIAgent,
        *,
        workflow: AgentWorkflow,
        approval: WorkflowApproval | None,
        legal_risk_card: dict[str, Any] | None = None,
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
            "citations": LangGraphEngine.public_citations(state),
            "tools_executed": LangGraphEngine.sanitize_tool_calls(state),
            "approval_card": approval_card,
            "hr_card": None,
            "jira_card": None,
            "legal_risk_card": legal_risk_card,
            "invoice_card": None,
            "quote_card": None,
            "dag_plan_card": None,
            # Drawn from the tool results the answer was written from, never from the
            # answer's text.
            "chart_cards": charts_from_tool_calls(state.get("tool_calls") or []),
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
