"""
Unit tests for Dashboard & Analytics API endpoints (/api/v1/dashboard).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import AIAgent, ChatConversation, ChatMessage, User
from app.api.v1.dashboard import DASHBOARD_TIMEZONE, _period_bounds


def test_get_dashboard_stats(client, ceo_token_headers, transactional_db_session):
    """Test fetching real dashboard stats with authentic DB counts."""
    user = transactional_db_session.query(User).filter(
        User.email == "admin@company.com"
    ).one()
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id
    ).first()
    conversation = ChatConversation(
        tenant_id=user.tenant_id,
        user_id=user.id,
        ai_agent_id=agent.id,
        title="Dashboard message count test",
        thread_id=str(uuid.uuid4()),
    )
    transactional_db_session.add(conversation)
    transactional_db_session.flush()
    transactional_db_session.add_all(
        [
            ChatMessage(
                conversation_id=conversation.id,
                sender="USER",
                content="Hello",
            ),
            ChatMessage(
                conversation_id=conversation.id,
                sender="ASSISTANT",
                content="Hi",
            ),
        ]
    )
    transactional_db_session.commit()

    res = client.get("/api/v1/dashboard/stats?period=week", headers=ceo_token_headers)
    assert res.status_code == 200, f"Dashboard stats failed: {res.text}"
    data = res.json()
    
    assert "kpi" in data
    assert "monthly_summary" in data
    assert "chatbots" in data
    assert "top_employees" in data
    assert "usage_trend" in data
    assert "monthly_data" in data

    assert data["kpi"]["total_agents"] >= 1
    assert data["kpi"]["total_employees"] >= 1
    # The endpoint counts messages inside the requested period, so the expectation has to
    # use the same window. Comparing against an unfiltered count only matched on a freshly
    # seeded database: as soon as the database held messages older than the period, the
    # two numbers diverged and this assertion failed for reasons that had nothing to do
    # with the endpoint. `_period_bounds` is the endpoint's own helper, so the window is
    # shared rather than re-derived here.
    period_start, period_end, _ = _period_bounds("week")
    assert data["kpi"]["total_messages"] == transactional_db_session.query(
        ChatMessage
    ).join(ChatConversation).filter(
        ChatConversation.tenant_id == user.tenant_id,
        ChatMessage.created_at >= period_start,
        ChatMessage.created_at <= period_end,
    ).count()
    assert isinstance(data["chatbots"], list)
    assert next(
        item for item in data["chatbots"] if item["id"] == str(agent.id)
    )["conversations"] >= 1
    assert len(data["usage_trend"]) == 7
    assert len(data["monthly_data"]) == 12


def test_dashboard_period_filters_and_trend_shape(
    client, ceo_token_headers, transactional_db_session
):
    """Day/week/month must query different local-time windows."""
    user = transactional_db_session.query(User).filter(
        User.email == "admin@company.com"
    ).one()
    agent = transactional_db_session.query(AIAgent).filter(
        AIAgent.tenant_id == user.tenant_id
    ).first()
    now = datetime.now(timezone.utc)
    conversation = ChatConversation(
        tenant_id=user.tenant_id,
        user_id=user.id,
        ai_agent_id=agent.id,
        title="Historical dashboard period test",
        thread_id=str(uuid.uuid4()),
        created_at=now - timedelta(days=2),
    )
    transactional_db_session.add(conversation)
    transactional_db_session.flush()
    transactional_db_session.add_all(
        [
            ChatMessage(
                conversation_id=conversation.id,
                sender="USER",
                content="Message from two days ago",
                created_at=now - timedelta(days=2),
            ),
            ChatMessage(
                conversation_id=conversation.id,
                sender="ASSISTANT",
                content="Historical response",
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    transactional_db_session.commit()

    day = client.get(
        "/api/v1/dashboard/stats?period=day", headers=ceo_token_headers
    ).json()
    week = client.get(
        "/api/v1/dashboard/stats?period=week", headers=ceo_token_headers
    ).json()
    month = client.get(
        "/api/v1/dashboard/stats?period=month", headers=ceo_token_headers
    ).json()

    assert week["kpi"]["total_messages"] == day["kpi"]["total_messages"] + 2
    assert len(day["usage_trend"]) == 24
    assert len(week["usage_trend"]) == 7
    assert len(month["usage_trend"]) == datetime.now(DASHBOARD_TIMEZONE).day
    assert week["period"]["timezone"] == "Asia/Ho_Chi_Minh"
    ceo_usage = next(
        item for item in week["top_employees"] if item["name"] == user.full_name
    )
    assert 0 < ceo_usage["pct"] <= 100


def test_dashboard_day_boundary_uses_vietnam_timezone():
    local_now = datetime(2026, 7, 31, 0, 30, tzinfo=DASHBOARD_TIMEZONE)
    start, end, returned_local = _period_bounds("day", local_now)

    assert start == datetime(2026, 7, 30, 17, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 30, 17, 30, tzinfo=timezone.utc)
    assert returned_local == local_now


def test_export_report_excel(client, ceo_token_headers):
    """Test Excel (.xlsx) report generation endpoint."""
    res = client.get("/api/v1/dashboard/reports/export/excel?period=week", headers=ceo_token_headers)
    assert res.status_code == 200
    assert "spreadsheetml" in res.headers["content-type"] or "csv" in res.headers["content-type"]
    assert len(res.content) > 0


def test_export_report_pdf(client, ceo_token_headers):
    """Test PDF report export endpoint."""
    res = client.get("/api/v1/dashboard/reports/export/pdf?period=week", headers=ceo_token_headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF") or len(res.content) > 0
