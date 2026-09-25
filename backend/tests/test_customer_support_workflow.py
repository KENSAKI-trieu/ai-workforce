import uuid

import redis

from app.models.models import (
    CustomerSupportCase,
    OutboundMessage,
    WorkflowApproval,
    WorkflowStepExecution,
)
from app.domains.support.support_workflow import execute_support_case
from app.domains.platform.work_queue import reserve_job


def test_idle_queue_timeout_is_not_a_worker_failure(monkeypatch):
    class IdleQueue:
        def brpoplpush(self, *args, **kwargs):
            raise redis.exceptions.TimeoutError("idle blocking pop")

    monkeypatch.setattr("app.domains.platform.work_queue._client", lambda: IdleQueue())
    assert reserve_job(timeout=1) is None


def test_support_workflow_is_async_approved_and_idempotent(
    client,
    transactional_db_session,
    employee_token_headers,
    ceo_token_headers,
    monkeypatch,
):
    queued: list[dict] = []

    def fake_enqueue(job_type, payload, dedup_key):
        queued.append({"type": job_type, "payload": payload, "key": dedup_key})
        return True

    monkeypatch.setattr(
        "app.api.v1.customer_support.enqueue_job", fake_enqueue
    )
    monkeypatch.setattr("app.api.v1.approvals.enqueue_job", fake_enqueue)
    monkeypatch.setattr(
        "app.api.v1.customer_support.queue_stats",
        lambda: {
            "available": True,
            "queued": 0,
            "processing": 0,
            "dead_letter": 0,
            "worker_online": True,
            "worker_last_seen": "2026-07-31T00:00:00+00:00",
        },
    )

    idempotency_key = f"email-{uuid.uuid4()}"
    payload = {
        "customer_email": "customer@example.com",
        "customer_name": "Khách hàng A",
        "subject": "Khiếu nại chính sách công tác phí",
        "body": "Tôi cần hỗ trợ vì chi phí di chuyển không được xử lý.",
        "priority": "HIGH",
    }
    response = client.post(
        "/api/v1/customer-support/cases",
        headers={
            **employee_token_headers,
            "Idempotency-Key": idempotency_key,
        },
        json=payload,
    )
    assert response.status_code == 202
    case_id = uuid.UUID(response.json()["id"])
    assert response.json()["status"] == "QUEUED"
    assert queued[0]["type"] == "support.execute"
    duplicate = client.post(
        "/api/v1/customer-support/cases",
        headers={**employee_token_headers, "Idempotency-Key": idempotency_key},
        json=payload,
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == str(case_id)
    assert len(queued) == 1

    assert execute_support_case(transactional_db_session, case_id) == "WAIT"
    case = transactional_db_session.query(CustomerSupportCase).filter(
        CustomerSupportCase.id == case_id
    ).one()
    assert case.status == "WAITING_APPROVAL"
    assert case.classification == "COMPLAINT"
    assert case.draft_reply

    approval = transactional_db_session.query(WorkflowApproval).filter(
        WorkflowApproval.workflow_id == case.workflow_id,
        WorkflowApproval.action_type == "SUPPORT_EMAIL_SEND",
    ).one()
    approved = client.post(
        f"/api/v1/approvals/{approval.id}/action",
        headers=ceo_token_headers,
        json={"action": "APPROVE", "comments": "Đã kiểm tra nguồn"},
    )
    assert approved.status_code == 200
    assert queued[-1]["type"] == "support.execute"

    assert execute_support_case(transactional_db_session, case_id) == "DONE"
    transactional_db_session.expire_all()
    case = transactional_db_session.query(CustomerSupportCase).filter(
        CustomerSupportCase.id == case_id
    ).one()
    assert case.status == "COMPLETED"
    assert transactional_db_session.query(OutboundMessage).filter(
        OutboundMessage.support_case_id == case_id
    ).count() == 1

    # At-least-once delivery can run the same job twice without a second email.
    assert execute_support_case(transactional_db_session, case_id) == "DONE"
    assert transactional_db_session.query(OutboundMessage).filter(
        OutboundMessage.support_case_id == case_id
    ).count() == 1
    assert transactional_db_session.query(WorkflowStepExecution).filter(
        WorkflowStepExecution.workflow_id == case.workflow_id,
        WorkflowStepExecution.status == "COMPLETED",
    ).count() == 7

    operations = client.get(
        "/api/v1/customer-support/operations",
        headers=ceo_token_headers,
    )
    assert operations.status_code == 200
    assert operations.json()["cases"]["completed"] == 1
    assert operations.json()["cases"]["success_rate"] == 1.0
    assert operations.json()["queue"]["worker_online"] is True
