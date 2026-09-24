"""Tests for the /auth/login throttle.

Redis is faked here: these assert the policy (how many attempts, what happens when the
store is unreachable), not redis-py's behaviour.
"""

import pytest
import redis

from app.core.config import settings
from app.domains.platform import login_rate_limit


class FakeRedis:
    """Enough of a Redis to count: INCR, EXPIRE, DELETE."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key: str, seconds: int) -> None:
        self.expiries[key] = seconds

    def delete(self, key: str) -> None:
        self.counters.pop(key, None)


class BrokenRedis:
    def incr(self, key: str) -> int:
        raise redis.ConnectionError("redis is down")

    def delete(self, key: str) -> None:
        raise redis.ConnectionError("redis is down")


@pytest.fixture
def limiter_on(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_MAX_PER_EMAIL", 3)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_MAX_PER_IP", 100)
    fake = FakeRedis()
    monkeypatch.setattr(login_rate_limit, "_client", lambda: fake)
    return fake


def test_attempts_are_allowed_up_to_the_budget(limiter_on):
    for _ in range(3):
        login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")


def test_the_next_attempt_after_the_budget_is_refused(limiter_on):
    for _ in range(3):
        login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")

    with pytest.raises(Exception) as exc:
        login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_the_budget_is_per_email(limiter_on):
    for _ in range(3):
        login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")
    # A different account is untouched by the first one's spent budget.
    login_rate_limit.enforce_login_rate_limit(None, "someone-else@company.com")


def test_the_email_is_matched_case_insensitively(limiter_on):
    for _ in range(3):
        login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")
    with pytest.raises(Exception) as exc:
        login_rate_limit.enforce_login_rate_limit(None, "  VICTIM@Company.com ")
    assert exc.value.status_code == 429


def test_a_successful_login_clears_the_counter(limiter_on):
    for _ in range(3):
        login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")
    login_rate_limit.clear_login_rate_limit("victim@company.com")
    # The budget is available again for someone who simply mistyped their password.
    login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")


def test_the_window_is_set_once_when_the_counter_starts(limiter_on):
    login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")
    assert limiter_on.expiries == {
        "auth:login:email:victim@company.com": settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    }


def test_an_unreachable_redis_refuses_the_attempt_by_default(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_FAIL_OPEN", False)
    monkeypatch.setattr(login_rate_limit, "_client", lambda: BrokenRedis())

    with pytest.raises(Exception) as exc:
        login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")
    assert exc.value.status_code == 503


def test_fail_open_is_available_but_must_be_chosen(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_FAIL_OPEN", True)
    monkeypatch.setattr(login_rate_limit, "_client", lambda: BrokenRedis())

    login_rate_limit.enforce_login_rate_limit(None, "victim@company.com")


def test_clearing_never_raises_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(login_rate_limit, "_client", lambda: BrokenRedis())

    login_rate_limit.clear_login_rate_limit("victim@company.com")


def test_login_endpoint_returns_429_once_the_budget_is_spent(client, monkeypatch):
    """End to end through the route, not just the helper."""
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_MAX_PER_EMAIL", 2)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_MAX_PER_IP", 100)
    # One instance for the whole test: a fresh FakeRedis per call would reset the counter
    # being asserted on.
    fake = FakeRedis()
    monkeypatch.setattr(login_rate_limit, "_client", lambda: fake)

    payload = {"email": "admin@company.com", "password": "WrongPassword!"}
    assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    assert client.post("/api/v1/auth/login", json=payload).status_code == 429
