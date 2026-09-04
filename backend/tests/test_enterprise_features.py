"""Regression tests for enterprise RBAC, task scopes and core workflow features."""

import uuid
from io import BytesIO

from app.models.models import AIAgent, User
from reportlab.pdfgen import canvas


def test_public_registration_always_creates_ceo_workspace(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"owner-{uuid.uuid4().hex[:8]}@example.com",
            "full_name": "Workspace Owner",
            "password": "Password123!",
            "tenant_name": "Isolated Workspace",
            "role": "CEO",
            "department": "ALL",
        },
    )
    assert response.status_code == 201
    # The founder holds the root position, which the default tree names "CEO". The legacy
    # role string says the same thing, so the two can no longer disagree.
    assert response.json()["user"]["role"] == "CEO"
    assert response.json()["user"]["position_name"] == "CEO"
    assert response.json()["user"]["department"] == "BOARD"


def test_locked_account_token_is_rejected(
    client, employee_token_headers, transactional_db_session
):
    employee = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).first()
    employee.is_active = False
    transactional_db_session.commit()
    try:
        response = client.get("/api/v1/users/me", headers=employee_token_headers)
        assert response.status_code == 403
    finally:
        employee.is_active = True
        transactional_db_session.commit()


def test_employee_cannot_list_organization(client, employee_token_headers):
    response = client.get("/api/v1/users-mgmt", headers=employee_token_headers)
    assert response.status_code == 403


def test_user_management_pagination_filters_and_update_response(
    client, ceo_token_headers, transactional_db_session
):
    owner = transactional_db_session.query(User).filter(
        User.email == "admin@company.com"
    ).one()
    for index in range(35):
        transactional_db_session.add(User(
            tenant_id=owner.tenant_id,
            email=f"page-filter-{index}-{uuid.uuid4().hex[:6]}@example.com",
            full_name=f"Page Filter Employee {index:02d}",
            password_hash=owner.password_hash,
            role="Employee" if index % 2 == 0 else "Manager",
            department="HR" if index % 3 == 0 else "IT",
            is_active=True,
        ))
    transactional_db_session.commit()

    first_page = client.get(
        "/api/v1/users-mgmt?page=1&page_size=30",
        headers=ceo_token_headers,
    )
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 30
    assert first_page.json()["pagination"]["page_size"] == 30
    assert first_page.json()["pagination"]["total"] >= 35

    second_page = client.get(
        "/api/v1/users-mgmt?page=2&page_size=30",
        headers=ceo_token_headers,
    )
    assert second_page.status_code == 200
    assert second_page.json()["items"]

    filtered = client.get(
        "/api/v1/users-mgmt",
        params={
            "page": 1,
            "page_size": 30,
            "q": "Page Filter",
            "department": "HR",
            "role": "Employee",
        },
        headers=ceo_token_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"]
    assert all(
        item["department"] == "HR" and item["role"] == "Employee"
        for item in filtered.json()["items"]
    )

    target = filtered.json()["items"][0]
    updated = client.patch(
        f"/api/v1/users-mgmt/{target['id']}/status",
        headers=ceo_token_headers,
        json={"department": "IT", "role": "Manager"},
    )
    assert updated.status_code == 200
    assert updated.json()["user"]["id"] == target["id"]
    assert updated.json()["user"]["department"] == "IT"
    assert updated.json()["user"]["role"] == "Manager"


def test_employee_task_scope(client, ceo_token_headers, employee_token_headers):
    employee = client.get("/api/v1/users/", headers=ceo_token_headers).json()
    employee_id = next(item["id"] for item in employee if item["email"] == "employee@company.com")
    private_task = client.post(
        "/api/v1/tasks",
        headers=ceo_token_headers,
        json={"title": "Board-only task", "status": "DRAFT"},
    )
    assigned_task = client.post(
        "/api/v1/tasks",
        headers=ceo_token_headers,
        json={
            "title": "Assigned employee task",
            "assignee_id": employee_id,
            "status": "PENDING",
        },
    )
    assert private_task.status_code == 201
    assert assigned_task.status_code == 201

    visible = client.get("/api/v1/tasks", headers=employee_token_headers)
    assert visible.status_code == 200
    titles = {item["title"] for item in visible.json()}
    assert "Assigned employee task" in titles
    assert "Board-only task" not in titles


def test_task_response_exposes_valid_transitions_and_terminal_task_is_immutable(
    client, ceo_token_headers
):
    created = client.post(
        "/api/v1/tasks",
        headers=ceo_token_headers,
        json={"title": "Task lifecycle contract", "status": "PENDING"},
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    pending = client.get(f"/api/v1/tasks/{task_id}", headers=ceo_token_headers)
    assert pending.status_code == 200
    assert pending.json()["allowed_transitions"] == [
        "RUNNING", "CANCELLED", "OVERDUE"
    ]

    running = client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=ceo_token_headers,
        json={"status": "RUNNING"},
    )
    assert running.status_code == 200
    completed = client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=ceo_token_headers,
        json={"status": "COMPLETED"},
    )
    assert completed.status_code == 200

    terminal = client.get(f"/api/v1/tasks/{task_id}", headers=ceo_token_headers)
    assert terminal.json()["status"] == "COMPLETED"
    assert terminal.json()["allowed_transitions"] == []

    invalid = client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=ceo_token_headers,
        json={"status": "WAITING_APPROVAL"},
    )
    assert invalid.status_code == 409
    assert invalid.json()["detail"] == (
        "Invalid task transition: COMPLETED -> WAITING_APPROVAL"
    )


def test_completed_task_can_be_deleted_but_active_task_cannot(
    client, ceo_token_headers
):
    active = client.post(
        "/api/v1/tasks",
        headers=ceo_token_headers,
        json={"title": "Active task cannot be deleted", "status": "PENDING"},
    )
    active_id = active.json()["task_id"]
    rejected = client.delete(
        f"/api/v1/tasks/{active_id}", headers=ceo_token_headers
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == (
        "Only DRAFT or COMPLETED tasks can be deleted; "
        "cancel active tasks instead"
    )

    running = client.patch(
        f"/api/v1/tasks/{active_id}",
        headers=ceo_token_headers,
        json={"status": "RUNNING"},
    )
    assert running.status_code == 200
    completed = client.patch(
        f"/api/v1/tasks/{active_id}",
        headers=ceo_token_headers,
        json={"status": "COMPLETED"},
    )
    assert completed.status_code == 200

    deleted = client.delete(
        f"/api/v1/tasks/{active_id}", headers=ceo_token_headers
    )
    assert deleted.status_code == 200
    assert client.get(
        f"/api/v1/tasks/{active_id}", headers=ceo_token_headers
    ).status_code == 404


def test_agent_tool_policy_is_enforced(
    client, employee_token_headers, transactional_db_session
):
    employee = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).one()
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id,
        AIAgent.role_code == "HR",
    ).first()
    original_denied = list(agent.disallowed_actions or [])
    agent.disallowed_actions = ["query_leave_balance"]
    transactional_db_session.commit()
    try:
        response = client.post(
            "/api/v1/agent/chat",
            headers=employee_token_headers,
            json={"agent_role": "HR", "message": "Tôi còn bao nhiêu ngày phép?"},
        )
        assert response.status_code == 403
    finally:
        agent.disallowed_actions = original_denied
        transactional_db_session.commit()


def test_workspace_departments_and_workflow_run(client, ceo_token_headers):
    code = f"QA_{uuid.uuid4().hex[:6].upper()}"
    department = client.post(
        "/api/v1/workspace/departments",
        headers=ceo_token_headers,
        json={"code": code, "name": "Quality Assurance"},
    )
    assert department.status_code == 201
    departments = client.get(
        "/api/v1/workspace/departments", headers=ceo_token_headers
    )
    assert code in {item["code"] for item in departments.json()}

    workflow = client.post(
        "/api/v1/workflows",
        headers=ceo_token_headers,
        json={
            "title": "Approval workflow",
            "trigger_type": "MANUAL",
            "nodes": [
                {
                    "id": "trigger",
                    "type": "TRIGGER",
                    "name": "Manual start",
                    "next": ["approval"],
                },
                {
                    "id": "approval",
                    "type": "HUMAN_APPROVAL",
                    "name": "Manager approval",
                    "config": {"risk_level": "HIGH"},
                },
            ],
        },
    )
    assert workflow.status_code == 201
    run = client.post(
        f"/api/v1/workflows/{workflow.json()['id']}/run",
        headers=ceo_token_headers,
    )
    assert run.status_code == 201
    assert run.json()["status"] == "AWAITING_APPROVAL"
    pending = client.get("/api/v1/approvals/pending", headers=ceo_token_headers)
    assert any(item["workflow_id"] == run.json()["id"] for item in pending.json())


def test_chat_history_feedback_and_task_conversion(client, employee_token_headers):
    chat = client.post(
        "/api/v1/agent/chat",
        headers=employee_token_headers,
        json={"agent_role": "KNOWLEDGE", "message": "Quy định công tác phí là gì?"},
    )
    assert chat.status_code == 200
    data = chat.json()
    conversation = client.get(
        f"/api/v1/agent/conversations/{data['conversation_id']}",
        headers=employee_token_headers,
    )
    assert conversation.status_code == 200
    assert len(conversation.json()["messages"]) >= 2
    feedback = client.post(
        f"/api/v1/agent/messages/{data['message_id']}/feedback",
        headers=employee_token_headers,
        json={"rating": 1, "comment": "Useful"},
    )
    assert feedback.status_code == 200
    task = client.post(
        f"/api/v1/agent/conversations/{data['conversation_id']}/task",
        headers=employee_token_headers,
        json={"title": "Follow up on travel policy"},
    )
    assert task.status_code == 201


def test_pdf_upload_is_checkpointed_and_indexed(client, ceo_token_headers):
    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 760, "Internal warranty policy: twelve months.")
    document.save()
    response = client.post(
        "/api/v1/documents/upload",
        headers=ceo_token_headers,
        data={
            "collection_name": "Product Policies",
            "department_access": "ALL",
            "duplicate_strategy": "replace",
        },
        files={"file": ("warranty-policy.pdf", buffer.getvalue(), "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "INDEXED"
    assert response.json()["chunks_created"] >= 1


def test_website_import_blocks_private_networks(client, ceo_token_headers):
    response = client.post(
        "/api/v1/documents/import-website",
        headers=ceo_token_headers,
        json={"url": "http://127.0.0.1:8000/docs", "department_access": "ALL"},
    )
    assert response.status_code == 422
    assert "Private or reserved" in response.json()["detail"]
