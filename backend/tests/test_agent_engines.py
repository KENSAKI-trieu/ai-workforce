"""The engine is chosen per AI Employee, the graph remembers the conversation, and a
graph that fails while streaming falls back like the non-streaming path does."""

from __future__ import annotations

import json
import uuid

import pytest

from app.clients.ai_service_client import AIServiceError
from app.core import agent_engines
from app.core.agent_engines import engine_for, parse_agent_engines, uses_langgraph
from app.core.config import settings
from app.agents.langgraph.engine import HISTORY_MESSAGE_CHARS, LangGraphEngine
from app.models.models import AIAgent, ChatConversation, ChatMessage, User


@pytest.fixture
def engines(monkeypatch):
    def configure(raw: str = "", *, langgraph_enabled: bool = False) -> None:
        monkeypatch.setattr(settings, "AGENT_ENGINES", raw)
        monkeypatch.setattr(settings, "LANGGRAPH_ENABLED", langgraph_enabled)
    return configure


def test_parsing_keeps_valid_entries_and_skips_the_rest() -> None:
    assert parse_agent_engines(" knowledge=LangGraph , legal = deterministic, bogus, hr=fast ") == {
        "KNOWLEDGE": "langgraph",
        "LEGAL": "deterministic",
    }
    assert parse_agent_engines(None) == {}


def test_one_role_moves_while_the_others_stay(engines) -> None:
    engines("KNOWLEDGE=langgraph")
    assert uses_langgraph("KNOWLEDGE")
    assert not uses_langgraph("LEGAL")


def test_the_global_switch_is_the_default_and_a_role_can_opt_out(engines) -> None:
    engines("LEGAL=deterministic", langgraph_enabled=True)
    assert uses_langgraph("KNOWLEDGE")
    assert not uses_langgraph("LEGAL")


def test_hr_and_unfinished_agents_never_reach_the_graph(engines) -> None:
    engines("HR=langgraph,FINANCE=langgraph,CEO=langgraph", langgraph_enabled=True)
    assert engine_for("HR") == agent_engines.DETERMINISTIC
    assert engine_for("FINANCE") == agent_engines.DETERMINISTIC
    assert engine_for("CEO") == agent_engines.DETERMINISTIC


def test_the_graph_payload_carries_the_earlier_turns(transactional_db_session) -> None:
    db = transactional_db_session
    user = db.query(User).filter(User.email == "admin@company.com").one()
    agent = db.query(AIAgent).filter(AIAgent.tenant_id == user.tenant_id, AIAgent.role_code == "KNOWLEDGE").one()
    conversation = ChatConversation(
        id=uuid.uuid4(), tenant_id=user.tenant_id, user_id=user.id, ai_agent_id=agent.id, title="t"
    )
    db.add(conversation)
    db.flush()
    turns = [
        ("USER", "Phòng HR có bao nhiêu người?"),
        ("ASSISTANT", "Phòng HR có 5 người."),
        ("USER", "x" * (HISTORY_MESSAGE_CHARS + 500)),
        ("ASSISTANT", "Đó là đoạn rất dài."),
        ("USER", "Còn phòng IT thì sao?"),  # this turn, stored before the engine runs
    ]
    for sender, content in turns:
        db.add(ChatMessage(conversation_id=conversation.id, sender=sender, content=content))
        db.flush()

    payload = LangGraphEngine._payload(
        db=db,
        user=user,
        agent=agent,
        conversation_id=str(conversation.id),
        workflow_id=str(uuid.uuid4()),
        message="Còn phòng IT thì sao?",
    )
    history = payload["history"]
    assert [item["role"] for item in history] == ["user", "assistant", "user", "assistant"]
    assert history[0]["content"] == "Phòng HR có bao nhiêu người?"
    assert history[-1]["content"] == "Đó là đoạn rất dài."
    assert len(history[2]["content"]) <= HISTORY_MESSAGE_CHARS + 4
    assert all(item["content"] != "Còn phòng IT thì sao?" for item in history)


def test_a_graph_that_fails_mid_stream_falls_back_to_the_deterministic_flow(
    client, employee_token_headers, engines, monkeypatch
) -> None:
    engines("KNOWLEDGE=langgraph")
    calls = {"graph": 0}

    def failing_stream(self, **kwargs):
        calls["graph"] += 1
        yield {"event": "status", "phase": "ANALYZING"}
        raise AIServiceError("AI service unavailable", status_code=503)

    def no_second_attempt(self, **kwargs):
        raise AssertionError("the fallback must not try the graph again")

    monkeypatch.setattr(LangGraphEngine, "execute_stream", failing_stream)
    monkeypatch.setattr(LangGraphEngine, "execute", no_second_attempt)
    response = client.post(
        "/api/v1/agent/chat/stream",
        json={"agent_role": "KNOWLEDGE", "message": "Chính sách nghỉ phép năm là gì?"},
        headers=employee_token_headers,
    )
    assert response.status_code == 200
    assert calls["graph"] == 1
    payloads = [
        json.loads(line[len("data:"):])
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    replies = [item.get("reply") for item in payloads if isinstance(item, dict) and "reply" in item]
    assert replies and replies[-1]
