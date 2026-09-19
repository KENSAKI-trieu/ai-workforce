"""
Tests for Sprint 3 Specialized AI Employees: Legal Agent, IT Agent, Finance Agent, and Sales Agent.
"""

from app.models.models import AIAgent, User

CONTRACT_FOR_AUDIT = (
    "HỢP ĐỒNG DỊCH VỤ\n"
    "Điều 1. Phạt vi phạm\nMức phạt vi phạm 30% giá trị hợp đồng.\n"
    "Điều 2. Chấm dứt\nBên A đơn phương chấm dứt hợp đồng ngay lập tức.\n"
    "Điều 3. Trách nhiệm\nBên B chịu trách nhiệm không giới hạn."
)


def test_legal_agent_asks_for_perspective_before_auditing(client, employee_token_headers):
    """The first turn must not review: severity depends on which party is asking."""
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "LEGAL", "message": CONTRACT_FOR_AUDIT},
        headers=employee_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["agent_role"] == "LEGAL"
    assert data["legal_risk_card"]["status"] == "COLLECTING"
    assert "Bên A" in data["reply"]
    assert "audit_contract_risk" not in [
        item["tool_name"] for item in data["tools_executed"]
    ]


def test_legal_agent_contract_audit(client, employee_token_headers):
    """Answering the perspective runs the audit and saves a reviewable record."""
    first = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "LEGAL", "message": CONTRACT_FOR_AUDIT},
        headers=employee_token_headers,
    )
    assert first.status_code == 200

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "agent_role": "LEGAL",
            "message": "Tôi là bên A",
            "conversation_id": first.json()["conversation_id"],
        },
        headers=employee_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["agent_role"] == "LEGAL"
    assert data["legal_risk_card"] is not None
    card = data["legal_risk_card"]
    assert card["total_risks_found"] > 0
    assert card["represented_party"] == "PARTY_A"
    # The old response advertised a redline URL that could only ever 404.
    assert "docx_download_url" not in card
    assert card["review_id"] and card["redline_url"]
    assert len(data["tools_executed"]) > 0
    assert data["tools_executed"][0]["tool_name"] == "audit_contract_risk"


def test_legal_agent_without_audit_tool_still_returns_chat_response(
    client, employee_token_headers, transactional_db_session
):
    employee = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).one()
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == employee.tenant_id,
        AIAgent.role_code == "LEGAL",
    ).one()
    original_tools = list(agent.tools_access or [])
    original_allowed = list(agent.allowed_actions or [])
    original_denied = list(agent.disallowed_actions or [])
    try:
        agent.tools_access = []
        agent.allowed_actions = []
        agent.disallowed_actions = []
        transactional_db_session.commit()

        response = client.post(
            "/api/v1/agent/chat",
            json={"agent_role": "LEGAL", "message": "Xin chào Legal Agent"},
            headers=employee_token_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tools_executed"] == []
        assert data["legal_risk_card"] is None
        assert "audit_contract_risk" in data["reply"]
    finally:
        agent.tools_access = original_tools
        agent.allowed_actions = original_allowed
        agent.disallowed_actions = original_denied
        transactional_db_session.commit()


def test_it_agent_jira_ticket(client, employee_token_headers):
    """Test IT Agent handling technical error report and creating Jira Ticket card."""
    payload = {
        "agent_role": "IT",
        "message": "VPN bị đứt kết nối khẩn cấp không thể kết nối mạng công ty",
    }
    response = client.post("/api/v1/agent/chat", json=payload, headers=employee_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_role"] == "IT"
    assert data["jira_card"] is not None
    card = data["jira_card"]
    assert "IT-" in card["ticket_key"]
    assert card["priority"] == "HIGH"
    assert card["status"] == "OPEN"
    assert len(data["tools_executed"]) > 0


def test_finance_agent_invoice_audit(client, employee_token_headers):
    """Test Finance Agent auditing invoice text and reconciling PO database."""
    payload = {
        "agent_role": "FINANCE",
        "message": "Hóa đơn PO-2025-098 tổng tiền 15.000.000 VNĐ từ Công ty TNHH Thiết Bị Số",
    }
    response = client.post("/api/v1/agent/chat", json=payload, headers=employee_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_role"] == "FINANCE"
    assert data["invoice_card"] is not None
    card = data["invoice_card"]
    assert card["po_number"] == "PO-2025-098"
    assert len(card["anomalies"]) > 0
    assert card["status"] == "DISCREPANCY_FLAGGED"


def test_sales_agent_quotation(client, employee_token_headers):
    """Test Sales Agent generating product quotation payload and PDF card."""
    payload = {
        "agent_role": "SALES",
        "message": "Tôi muốn xin báo giá 20 camera AI IP Security",
    }
    response = client.post("/api/v1/agent/chat", json=payload, headers=employee_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_role"] == "SALES"
    assert data["quote_card"] is not None
    card = data["quote_card"]
    assert "Camera AI" in card["items"][0]["name"]
    assert card["items"][0]["quantity"] == 20
    assert "pdf_url" in card
