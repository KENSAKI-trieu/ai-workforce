"""Regression coverage for analytics, audit, settings, notifications and integrations."""

import uuid


def test_management_dashboard_is_actionable_and_rbac_scoped(
    client, ceo_token_headers, employee_token_headers
):
    response = client.get(
        "/api/v1/management/dashboard?period=30d", headers=ceo_token_headers
    )
    assert response.status_code == 200, response.text
    data = response.json()
    expected = {
        "tasks_completed",
        "success_rate",
        "average_execution_seconds",
        "human_approval_rate",
        "pending_approvals",
        "failed_workflows",
        "token_usage",
        "estimated_cost_usd",
        "hours_saved",
        "active_agents",
        "user_satisfaction",
    }
    assert expected <= set(data["kpis"])
    assert "attention" in data["task_health"]
    assert "hours_saved" in data["methodology"]

    forbidden = client.get(
        "/api/v1/management/dashboard", headers=employee_token_headers
    )
    assert forbidden.status_code == 403


def test_workspace_settings_validation_export_and_audit(client, ceo_token_headers):
    invalid_timezone = client.patch(
        "/api/v1/workspace",
        headers=ceo_token_headers,
        json={"timezone": "Mars/Olympus"},
    )
    assert invalid_timezone.status_code == 422

    updated = client.patch(
        "/api/v1/workspace",
        headers={
            **ceo_token_headers,
            "User-Agent": "Enterprise-Test-Device/1.0",
            "X-Forwarded-For": "203.0.113.15",
        },
        json={
            "timezone": "Asia/Ho_Chi_Minh",
            "language": "vi",
            "data_retention_days": 730,
            "default_model": "gpt-4o",
            "notification_settings": {
                "approval_notifications": True,
                "task_notifications": True,
                "cost_notifications": True,
                "integration_notifications": True,
            },
            "security_settings": {
                "mfa_required": True,
                "session_timeout_minutes": 120,
                "allowed_email_domains": ["company.com"],
                "ip_allowlist": ["10.0.0.0/8"],
            },
        },
    )
    assert updated.status_code == 200, updated.text

    export = client.get("/api/v1/workspace/export", headers=ceo_token_headers)
    assert export.status_code == 200
    serialized = str(export.json()).lower()
    assert "password_hash" not in serialized
    assert "credential_reference" not in serialized

    events = client.get(
        "/api/v1/audit/events?action=workspace.settings_updated",
        headers=ceo_token_headers,
    )
    assert events.status_code == 200
    matching = events.json()["items"]
    assert matching
    assert matching[0]["actor"]["email"] == "admin@company.com"
    assert matching[0]["ip_address"] == "203.0.113.15"
    assert matching[0]["before_data"] is not None
    assert matching[0]["after_data"] is not None


def test_notification_preferences_and_tenant_user_scope(
    client, ceo_token_headers, employee_token_headers
):
    preferences = client.get(
        "/api/v1/notifications/preferences", headers=employee_token_headers
    )
    assert preferences.status_code == 200
    catalog = preferences.json()["event_catalog"]
    assert "TASK_FAILED" in catalog
    assert "INTEGRATION_DISCONNECTED" in catalog

    update = client.put(
        "/api/v1/notifications/preferences",
        headers=employee_token_headers,
        json={
            "enabled_event_types": ["TASK_COMPLETED", "TASK_FAILED"],
            "enabled_channels": ["IN_APP"],
            "quiet_hours": {
                "enabled": True,
                "start": "22:00",
                "end": "07:00",
                "timezone": "Asia/Ho_Chi_Minh",
            },
        },
    )
    assert update.status_code == 200

    scan = client.post("/api/v1/notifications/scan", headers=employee_token_headers)
    assert scan.status_code == 200
    inbox = client.get("/api/v1/notifications", headers=employee_token_headers)
    assert inbox.status_code == 200
    assert "unread_count" in inbox.json()

    if inbox.json()["items"]:
        notification_id = inbox.json()["items"][0]["id"]
        cross_user = client.post(
            f"/api/v1/notifications/{notification_id}/read",
            headers=ceo_token_headers,
        )
        assert cross_user.status_code == 404


def test_notification_can_only_be_deleted_by_its_owner(
    client, ceo_token_headers, employee_token_headers, transactional_db_session
):
    from app.models.models import Notification, User

    employee = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).one()
    notification = Notification(
        tenant_id=employee.tenant_id,
        user_id=employee.id,
        event_type="TASK_COMPLETED",
        title="Delete notification test",
        message="This notification belongs to the employee",
        severity="SUCCESS",
        payload={},
        is_read=False,
    )
    transactional_db_session.add(notification)
    transactional_db_session.commit()

    forbidden = client.delete(
        f"/api/v1/notifications/{notification.id}", headers=ceo_token_headers
    )
    assert forbidden.status_code == 404
    deleted = client.delete(
        f"/api/v1/notifications/{notification.id}", headers=employee_token_headers
    )
    assert deleted.status_code == 200


def test_integration_least_privilege_lifecycle(
    client, ceo_token_headers, employee_token_headers
):
    name = f"Support Slack {uuid.uuid4().hex[:6]}"
    wildcard = client.post(
        "/api/v1/integrations",
        headers=ceo_token_headers,
        json={
            "provider": "SLACK",
            "display_name": name,
            "auth_type": "TOKEN",
            "credential_reference": "env:SLACK_CREDENTIAL",
            "permissions": ["*"],
            "allowed_resources": ["channel:support"],
            "allowed_agent_roles": ["SALES"],
        },
    )
    assert wildcard.status_code == 422

    created = client.post(
        "/api/v1/integrations",
        headers=ceo_token_headers,
        json={
            "provider": "SLACK",
            "display_name": name,
            "auth_type": "TOKEN",
            "credential_reference": "env:SLACK_CREDENTIAL",
            "permissions": ["messages:read", "chat:write"],
            "allowed_resources": ["channel:support"],
            "allowed_agent_roles": ["SALES"],
            "configuration": {"workspace": "support"},
        },
    )
    assert created.status_code == 201, created.text
    connection = created.json()
    assert "credential_reference" not in connection
    assert connection["allowed_resources"] == ["channel:support"]

    employee_create = client.post(
        "/api/v1/integrations",
        headers=employee_token_headers,
        json={
            "provider": "NOTION",
            "display_name": "Blocked",
            "auth_type": "TOKEN",
            "credential_reference": "env:NOTION_CREDENTIAL",
            "permissions": ["read"],
            "allowed_resources": ["database:kb"],
        },
    )
    assert employee_create.status_code == 403

    tested = client.post(
        f"/api/v1/integrations/{connection['id']}/test",
        headers=ceo_token_headers,
    )
    assert tested.status_code == 200
    assert tested.json()["status"] == "CONNECTED"
    assert tested.json()["mode"] == "CONFIGURATION_VALIDATION"

    activity = client.get(
        f"/api/v1/integrations/{connection['id']}/activity",
        headers=ceo_token_headers,
    )
    assert activity.status_code == 200
    assert activity.json()[0]["operation"] == "CONFIGURATION_TEST"

    disconnected = client.post(
        f"/api/v1/integrations/{connection['id']}/disconnect",
        headers=ceo_token_headers,
    )
    assert disconnected.status_code == 200
    assert disconnected.json()["status"] == "DISCONNECTED"


def test_company_deletion_request_is_refused_below_the_ceo(
    client, ceo_token_headers, employee_token_headers
):
    """Only the person who runs the company may start a deletion.

    This used to be an Owner-only check that even refused the CEO. The founder's role
    string is "CEO" now, so the CEO is precisely who this must admit -- and everyone
    below them is still who it must turn away.
    """
    workspace = client.get("/api/v1/workspace", headers=ceo_token_headers).json()
    payload = {
        "confirmation_domain": workspace["domain"],
        "reason": "Test request must never delete company data immediately.",
    }

    refused = client.post(
        "/api/v1/workspace/data-deletion-request",
        headers=employee_token_headers,
        json=payload,
    )
    assert refused.status_code == 403

    accepted = client.post(
        "/api/v1/workspace/data-deletion-request",
        headers=ceo_token_headers,
        json=payload,
    )
    # 202: the request is queued for review, never an immediate delete.
    assert accepted.status_code == 202
