"""Durable Customer Support workflow with approval and idempotent delivery."""

import smtplib
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import (
    AgentWorkflow,
    AuditLog,
    CustomerSupportCase,
    OutboundMessage,
    Task,
    User,
    WorkflowApproval,
    WorkflowStepExecution,
)
from app.domains.platform.notification_service import notify_users
from app.domains.knowledge.rag_service import hybrid_search_documents

STEP_SPECS = (
    ("read_email", "TRIGGER", 30),
    ("classify", "AI_AGENT", 30),
    ("retrieve_policy", "RAG", 60),
    ("draft_reply", "AI_AGENT", 60),
    ("human_approval", "HUMAN_APPROVAL", 86400),
    ("send_email", "TOOL", 30),
    ("finalize", "OUTPUT", 30),
)


def initialize_steps(db: Session, case: CustomerSupportCase) -> None:
    for position, (key, step_type, timeout) in enumerate(STEP_SPECS):
        db.add(
            WorkflowStepExecution(
                tenant_id=case.tenant_id,
                workflow_id=case.workflow_id,
                step_key=key,
                step_type=step_type,
                position=position,
                timeout_seconds=timeout,
                max_attempts=3 if key == "send_email" else 1,
            )
        )


def _classify(text: str) -> tuple[str, float]:
    normalized = text.lower()
    buckets = {
        "REFUND": ("hoàn tiền", "refund", "chargeback"),
        "COMPLAINT": ("khiếu nại", "phàn nàn", "complaint", "không hài lòng"),
        "QUOTATION": ("báo giá", "quotation", "price", "pricing"),
        "SUPPORT": ("hỗ trợ", "lỗi", "không hoạt động", "support", "error"),
    }
    for category, keywords in buckets.items():
        if any(keyword in normalized for keyword in keywords):
            return category, 0.93
    return "GENERAL", 0.55


def _send(case: CustomerSupportCase, message: OutboundMessage) -> None:
    if message.status in {"SENT", "ACCEPTED"}:
        return
    message.attempt_count += 1
    if settings.EMAIL_DELIVERY_MODE.lower() == "outbox":
        message.status = "ACCEPTED"
        message.provider_message_id = f"outbox:{message.id}"
        message.sent_at = datetime.now(timezone.utc)
        return
    if settings.EMAIL_DELIVERY_MODE.lower() != "smtp":
        raise RuntimeError("EMAIL_DELIVERY_MODE must be 'outbox' or 'smtp'")
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        raise RuntimeError("SMTP_HOST and SMTP_FROM_EMAIL are required")
    email = EmailMessage()
    email["From"] = settings.SMTP_FROM_EMAIL
    email["To"] = case.customer_email
    email["Subject"] = message.subject
    email.set_content(message.body)
    with smtplib.SMTP(
        settings.SMTP_HOST, settings.SMTP_PORT, timeout=20
    ) as client:
        if settings.SMTP_USE_TLS:
            client.starttls()
        if settings.SMTP_USERNAME:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        client.send_message(email)
    message.status = "SENT"
    message.provider_message_id = email.get("Message-ID") or f"smtp:{message.id}"
    message.sent_at = datetime.now(timezone.utc)


def _approval_gate(db: Session, case: CustomerSupportCase, step: WorkflowStepExecution) -> str:
    approval = db.query(WorkflowApproval).filter(
        WorkflowApproval.workflow_id == case.workflow_id,
        WorkflowApproval.action_type == "SUPPORT_EMAIL_SEND",
    ).first()
    if approval and approval.status == "REJECTED":
        step.status = "REJECTED"
        step.finished_at = datetime.now(timezone.utc)
        case.status = "REJECTED"
        case.last_error = approval.comments or "Manager rejected the reply"
        return "STOP"
    if approval and approval.status == "APPROVED":
        step.status = "COMPLETED"
        step.output_data = {"approval_id": str(approval.id), "decision": "APPROVED"}
        step.finished_at = datetime.now(timezone.utc)
        return "CONTINUE"
    if not approval:
        approvers = db.query(User).filter(
            User.tenant_id == case.tenant_id,
            User.role.in_(("Owner", "Admin", "CEO", "Manager")),
            User.is_active.is_(True),
        ).all()
        approval = WorkflowApproval(
            workflow_id=case.workflow_id,
            action_type="SUPPORT_EMAIL_SEND",
            risk_level="HIGH" if case.classification in {"REFUND", "COMPLAINT"} else "MEDIUM",
            payload={
                "reason": "AI drafted a customer-facing email",
                "customer_email": case.customer_email,
                "subject": case.subject,
                "draft_reply": case.draft_reply,
                "data_sources": case.citations,
                "confidence": case.confidence,
                "support_case_id": str(case.id),
            },
            status="WAITING",
        )
        db.add(approval)
        db.flush()
        notify_users(
            db,
            approvers,
            event_type="APPROVAL_REQUIRED",
            title="Email hỗ trợ khách hàng cần phê duyệt",
            message=case.subject,
            severity="WARNING",
            entity_type="APPROVAL",
            entity_id=str(approval.id),
            dedup_key=f"support-approval:{approval.id}",
        )
    step.status = "WAITING"
    case.status = "WAITING_APPROVAL"
    return "WAIT"


def execute_support_case(db: Session, case_id: uuid.UUID) -> str:
    case = db.query(CustomerSupportCase).filter(CustomerSupportCase.id == case_id).first()
    if not case:
        return "MISSING"
    workflow = db.query(AgentWorkflow).filter(AgentWorkflow.id == case.workflow_id).one()
    task = db.query(Task).filter(Task.id == case.task_id).one()
    if case.status in {"COMPLETED", "REJECTED", "CANCELLED"}:
        return "DONE"
    case.status = "RUNNING"
    workflow.status = "IN_PROGRESS"
    task.status = "RUNNING"
    db.commit()

    steps = db.query(WorkflowStepExecution).filter(
        WorkflowStepExecution.workflow_id == workflow.id
    ).order_by(WorkflowStepExecution.position).all()
    for step in steps:
        if step.status == "COMPLETED":
            continue
        if step.step_key == "human_approval":
            result = _approval_gate(db, case, step)
            workflow.status = "AWAITING_APPROVAL" if result == "WAIT" else workflow.status
            task.status = "WAITING_APPROVAL" if result == "WAIT" else task.status
            db.commit()
            if result != "CONTINUE":
                return result
            continue
        step.status = "RUNNING"
        step.attempt_count += 1
        step.started_at = datetime.now(timezone.utc)
        # Persist the attempt before side effects so a rollback/crash cannot
        # reset the retry counter and create an infinite retry loop.
        db.commit()
        started = time.monotonic()
        try:
            if step.step_key == "read_email":
                step.output_data = {"subject": case.subject, "sender": case.customer_email}
            elif step.step_key == "classify":
                case.classification, case.confidence = _classify(
                    f"{case.subject}\n{case.inbound_body}"
                )
                step.output_data = {
                    "classification": case.classification,
                    "confidence": case.confidence,
                    "needs_review": case.confidence < 0.7,
                }
            elif step.step_key == "retrieve_policy":
                results = hybrid_search_documents(
                    db,
                    case.tenant_id,
                    case.inbound_body,
                    department="ALL",
                    top_k=4,
                    user_role="customer_support",
                )
                case.citations = [
                    {
                        "chunk_id": item["id"],
                        "document_id": item["document_id"],
                        "document_name": item["document_name"],
                        "document_title": item["document_title"],
                        "section_title": item["section_title"],
                        "version": item["version"],
                        "page": item["page"],
                        "score": item["score"],
                    }
                    for item in results
                ]
                step.output_data = {
                    "result_count": len(results),
                    "documents": case.citations,
                    "needs_review": not results,
                }
            elif step.step_key == "draft_reply":
                source_note = (
                    "\n\nNguồn tham khảo: "
                    + "; ".join(
                        f"{item['document_name']} — {item['section_title']}"
                        for item in case.citations
                    )
                    if case.citations
                    else "\n\nChưa tìm thấy chính sách phù hợp; cần nhân viên bổ sung."
                )
                case.draft_reply = (
                    f"Chào {case.customer_name or 'Quý khách'},\n\n"
                    f"Chúng tôi đã nhận yêu cầu “{case.subject}”. "
                    "Đội ngũ đang xử lý và sẽ phản hồi dựa trên chính sách đã xác minh."
                    f"{source_note}\n\nTrân trọng,\nCustomer Support"
                )
                step.output_data = {"draft_length": len(case.draft_reply)}
            elif step.step_key == "send_email":
                message = db.query(OutboundMessage).filter(
                    OutboundMessage.support_case_id == case.id,
                    OutboundMessage.idempotency_key == "approved-reply-v1",
                ).first()
                if not message:
                    message = OutboundMessage(
                        tenant_id=case.tenant_id,
                        support_case_id=case.id,
                        idempotency_key="approved-reply-v1",
                        recipient=case.customer_email,
                        subject=f"Re: {case.subject}",
                        body=case.draft_reply or "",
                        delivery_mode=settings.EMAIL_DELIVERY_MODE.lower(),
                    )
                    db.add(message)
                    db.flush()
                _send(case, message)
                step.output_data = {
                    "message_id": str(message.id),
                    "delivery_status": message.status,
                }
            elif step.step_key == "finalize":
                case.status = "COMPLETED"
                workflow.status = "COMPLETED"
                workflow.completed_at = datetime.now(timezone.utc)
                task.status = "COMPLETED"
                task.output_result = {
                    "support_case_id": str(case.id),
                    "classification": case.classification,
                    "citations": case.citations,
                }
                step.output_data = {"status": "COMPLETED"}
            elapsed = time.monotonic() - started
            if elapsed > step.timeout_seconds:
                raise TimeoutError(f"Step exceeded {step.timeout_seconds}s")
            step.status = "COMPLETED"
            step.finished_at = datetime.now(timezone.utc)
            step.error_message = None
            workflow.current_step = step.position + 1
            db.add(
                AuditLog(
                    tenant_id=case.tenant_id,
                    actor_type="AI",
                    workflow_id=workflow.id,
                    agent_role="CUSTOMER_SUPPORT",
                    tool_name=step.step_key,
                    action=f"support.{step.step_key}",
                    resource_type="SUPPORT_CASE",
                    resource_id=str(case.id),
                    output_result=step.output_data,
                    status="SUCCESS",
                    execution_time_ms=int(elapsed * 1000),
                )
            )
            db.commit()
        except Exception as error:
            db.rollback()
            step = db.query(WorkflowStepExecution).filter(
                WorkflowStepExecution.id == step.id
            ).one()
            case = db.query(CustomerSupportCase).filter(
                CustomerSupportCase.id == case_id
            ).one()
            workflow = db.query(AgentWorkflow).filter(
                AgentWorkflow.id == case.workflow_id
            ).one()
            task = db.query(Task).filter(Task.id == case.task_id).one()
            step.error_message = str(error)[:2000]
            case.last_error = step.error_message
            if step.attempt_count < step.max_attempts:
                step.status = "RETRY_PENDING"
                case.status = "RETRY_PENDING"
                db.commit()
                return "RETRY"
            step.status = "FAILED"
            step.finished_at = datetime.now(timezone.utc)
            case.status = workflow.status = task.status = "FAILED"
            db.commit()
            return "FAILED"
    return "DONE"
