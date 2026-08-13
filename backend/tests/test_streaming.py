import importlib


chat_api = importlib.import_module("app.api.v1.chat")


def test_chat_stream_emits_public_sse_contract(
    client,
    employee_token_headers,
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_api.settings, "LANGGRAPH_ENABLED", False)
    monkeypatch.setattr(chat_api, "execute_agent_chat", lambda **_: {
        "agent_name": "Knowledge Agent",
        "agent_role": "KNOWLEDGE",
        "avatar_emoji": "🤖",
        "reply": "Câu trả lời an toàn.",
        "citations": [],
        "tools_executed": [],
        "approval_card": None,
        "hr_card": None,
        "jira_card": None,
        "legal_risk_card": None,
        "invoice_card": None,
        "quote_card": None,
        "dag_plan_card": None,
    })

    response = client.post(
        "/api/v1/agent/chat/stream",
        headers=employee_token_headers,
        json={"agent_role": "KNOWLEDGE", "message": "Tóm tắt chính sách"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: status\ndata: {"phase": "ANALYZING"}' in response.text
    assert 'event: status\ndata: {"phase": "COMPLETED"}' in response.text
    assert "event: token" in response.text
    assert "event: complete" in response.text
    assert "chain_of_thought" not in response.text
    assert "system_prompt" not in response.text
