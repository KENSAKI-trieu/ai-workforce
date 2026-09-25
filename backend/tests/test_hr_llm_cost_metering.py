"""The HR agent's own model calls have to reach the cost dashboard.

Every other agent is metered by the internal tool gateway. HR calls the provider
directly -- up to three times per turn -- so without the reporter wired into
``hr_llm_flow`` the dashboard reports the busiest agent in the product as free. These
tests drive the real chat endpoint and then look for the row.
"""
from __future__ import annotations

import pytest

from app.models.models import LLMCostLog, User
from tests.chat_patching import patch_chat
from app.agents.hr import llm_flow as hr_llm_flow


class FakeAIClient:
    """Stands in for the AI service, answering every call with the same payload."""

    enabled = True

    def __init__(self, response: dict):
        self.response = response
        self.calls = 0

    def generate_text(self, _messages, **_kwargs):
        self.calls += 1
        return self.response


def use_fake_ai_service(monkeypatch, response: dict) -> FakeAIClient:
    client = FakeAIClient(response)
    monkeypatch.setattr(hr_llm_flow, "get_ai_service_client", lambda: client)
    return client


def hr_cost_rows(db, email: str) -> list[LLMCostLog]:
    actor = db.query(User).filter(User.email == email).one()
    return db.query(LLMCostLog).filter(
        LLMCostLog.tenant_id == actor.tenant_id,
        LLMCostLog.agent_role == "HR",
    ).all()


def ask(client, headers, message: str = "Tôi còn bao nhiêu ngày phép?") -> dict:
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "HR", "message": message},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_an_hr_chat_turn_records_its_token_usage(
    client, employee_token_headers, transactional_db_session, monkeypatch
):
    before = len(hr_cost_rows(transactional_db_session, "employee@company.com"))
    use_fake_ai_service(monkeypatch, {
        "content": '{"kind":"QUESTION","intent":"QUERY_LEAVE_BALANCE"}',
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage": {
            "prompt_tokens": 420,
            "completion_tokens": 12,
            "cached_prompt_tokens": 20,
        },
    })

    answer = ask(client, employee_token_headers)

    assert "ngày phép" in answer["reply"]
    rows = hr_cost_rows(transactional_db_session, "employee@company.com")
    assert len(rows) == before + 1
    recorded = rows[-1]
    assert recorded.model_name == "gpt-4o-mini"
    assert recorded.prompt_tokens == 420
    assert recorded.completion_tokens == 12
    assert recorded.cached_prompt_tokens == 20
    assert recorded.usage_source == "PROVIDER"
    assert float(recorded.estimated_cost_usd) > 0


def test_the_turn_shows_up_in_the_cost_dashboard_breakdown(
    client, employee_token_headers, ceo_token_headers, monkeypatch
):
    """End of the road: what the chat spends has to be visible where cost is reported."""
    use_fake_ai_service(monkeypatch, {
        "content": '{"kind":"QUESTION","intent":"QUERY_LEAVE_BALANCE"}',
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage": {"prompt_tokens": 900, "completion_tokens": 30},
    })

    ask(client, employee_token_headers)

    response = client.get("/api/v1/costs/by-agent", headers=ceo_token_headers)
    assert response.status_code == 200, response.text
    hr_group = next(
        item for item in response.json() if item["agent_role"] == "HR"
    )
    assert hr_group["total_tokens"] > 0
    assert "gpt-4o-mini" in hr_group["models_used"]


def test_the_recorded_row_names_the_employee_who_asked(
    client, employee_token_headers, transactional_db_session, monkeypatch
):
    """Cost per employee is one of the dashboard's breakdowns, so the row carries a user."""
    use_fake_ai_service(monkeypatch, {
        "content": '{"kind":"QUESTION","intent":"QUERY_LEAVE_BALANCE"}',
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage": {"prompt_tokens": 100, "completion_tokens": 5},
    })

    ask(client, employee_token_headers)

    actor = transactional_db_session.query(User).filter(
        User.email == "employee@company.com"
    ).one()
    recorded = hr_cost_rows(transactional_db_session, "employee@company.com")[-1]
    assert recorded.user_id == actor.id
    assert recorded.department == actor.department


def test_an_unpriced_model_is_skipped_without_failing_the_answer(
    client, employee_token_headers, transactional_db_session, monkeypatch
):
    """Pricing is deliberately never guessed, so the turn must survive the missing row."""
    before = len(hr_cost_rows(transactional_db_session, "employee@company.com"))
    use_fake_ai_service(monkeypatch, {
        "content": '{"kind":"QUESTION","intent":"QUERY_LEAVE_BALANCE"}',
        "provider": "gemini",
        "model": "model-nobody-configured-a-price-for",
        "usage": {"prompt_tokens": 100, "completion_tokens": 5},
    })

    answer = ask(client, employee_token_headers)

    assert "ngày phép" in answer["reply"]
    assert len(hr_cost_rows(transactional_db_session, "employee@company.com")) == before


def test_the_local_echo_provider_is_not_billed(
    client, employee_token_headers, transactional_db_session, monkeypatch
):
    before = len(hr_cost_rows(transactional_db_session, "employee@company.com"))
    use_fake_ai_service(monkeypatch, {
        "content": "Local provider received: Tôi còn bao nhiêu ngày phép?",
        "provider": "local",
        "model": "local-echo",
        "usage": {"prompt_tokens": 8, "completion_tokens": 4},
    })

    ask(client, employee_token_headers)

    assert len(hr_cost_rows(transactional_db_session, "employee@company.com")) == before


def test_a_provider_response_without_counters_is_not_recorded(
    client, employee_token_headers, transactional_db_session, monkeypatch
):
    """A zero-token row cannot be priced from anything real; it would only add noise."""
    before = len(hr_cost_rows(transactional_db_session, "employee@company.com"))
    use_fake_ai_service(monkeypatch, {
        "content": '{"kind":"QUESTION","intent":"QUERY_LEAVE_BALANCE"}',
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage": {},
    })

    ask(client, employee_token_headers)

    assert len(hr_cost_rows(transactional_db_session, "employee@company.com")) == before


def test_a_leave_request_turn_meters_both_of_its_model_calls(
    client, employee_token_headers, transactional_db_session, monkeypatch
):
    """Routing and slot extraction are two billed calls; the meter must see both."""
    before = len(hr_cost_rows(transactional_db_session, "employee@company.com"))
    fake = use_fake_ai_service(monkeypatch, {
        # Serves as both the router answer and the slot extractor's: neither parser
        # accepts the other's keys, and an unusable payload still bills its tokens.
        "content": '{"kind":"ACTION","intent":"ACTION_LEAVE_REQUEST"}',
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage": {"prompt_tokens": 200, "completion_tokens": 10},
    })

    ask(client, employee_token_headers, "Tôi muốn xin nghỉ phép")

    assert fake.calls == 2
    assert len(hr_cost_rows(transactional_db_session, "employee@company.com")) == before + 2


def test_metering_failure_never_costs_the_user_their_answer(
    client, employee_token_headers, monkeypatch
):
    """The reporter is telemetry: if it raises, the turn still has to be answered."""
    use_fake_ai_service(monkeypatch, {
        "content": '{"kind":"QUESTION","intent":"QUERY_LEAVE_BALANCE"}',
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage": {"prompt_tokens": 100, "completion_tokens": 5},
    })

    def exploding_recorder(_db, _user):
        def record(_result):
            raise RuntimeError("metering backend is down")

        return record

    patch_chat(monkeypatch, "_hr_llm_usage_recorder", exploding_recorder)

    answer = ask(client, employee_token_headers)

    assert "ngày phép" in answer["reply"]


@pytest.mark.parametrize(
    ("model", "expected_cost_usd"),
    [
        # 1M input + 1M output at the published mini price, and at the full gpt-4o price
        # the mini snapshot used to be billed at before it got its own row.
        ("gpt-4o-mini", 0.75),
        ("gpt-4o-mini-2024-07-18", 0.75),
        ("gpt-4o", 12.50),
        # Provisional, carried over from gemini-2.5-flash until the real price is known.
        ("gemini-3.6-flash", 2.80),
        # Provisional, carried over from gemini-2.5-flash-lite until the real price is known.
        ("gemini-3.5-flash-lite", 0.50),
    ],
)
def test_the_default_chat_model_is_priced_as_itself(model, expected_cost_usd):
    from app.domains.platform.cost_calculator import calculate_llm_cost

    cost = calculate_llm_cost(model, 1_000_000, 1_000_000)

    assert float(cost) == pytest.approx(expected_cost_usd)
