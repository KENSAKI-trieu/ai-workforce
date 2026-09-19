"""Throttle password guessing against /auth/login.

There was no limit at all: an attacker could try every password in a list against a
known address as fast as the server would answer. Two counters, both fixed windows in
Redis, because that is the infrastructure this deployment already has.

The email counter is the one that matters -- it caps guesses against a single account and
is cleared the moment that account logs in successfully, so a person who simply mistyped
their password a few times is not punished afterwards. The IP counter is a much looser
backstop against someone spraying one password across many accounts; it stays loose
because an entire office can arrive from a single NAT address.
"""

from __future__ import annotations

import logging
from typing import Optional

import redis
from fastapi import HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "auth:login"


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None or request.client is None:
        return None
    return request.client.host


def _hit(client: redis.Redis, key: str, limit: int) -> bool:
    """Count one attempt. Returns False once the window's budget is spent."""
    count = client.incr(key)
    if count == 1:
        client.expire(key, settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS)
    return count <= limit


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Login is temporarily unavailable. Please try again shortly.",
    )


def _too_many() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many login attempts. Please wait and try again.",
        headers={"Retry-After": str(settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS)},
    )


def enforce_login_rate_limit(request: Optional[Request], email: str) -> None:
    """Raise 429 when this email or address has spent its budget for the window."""
    if not settings.LOGIN_RATE_LIMIT_ENABLED:
        return

    email_key = f"{_KEY_PREFIX}:email:{email.strip().lower()}"
    ip = _client_ip(request)

    try:
        client = _client()
        within_email_budget = _hit(client, email_key, settings.LOGIN_RATE_LIMIT_MAX_PER_EMAIL)
        within_ip_budget = True
        if ip:
            within_ip_budget = _hit(
                client, f"{_KEY_PREFIX}:ip:{ip}", settings.LOGIN_RATE_LIMIT_MAX_PER_IP
            )
    except redis.RedisError:
        logger.error(
            "Login rate limiting is unavailable: Redis at %s could not be reached. "
            "%s. Set LOGIN_RATE_LIMIT_FAIL_OPEN to change this behaviour.",
            settings.REDIS_URL,
            "Allowing the attempt" if settings.LOGIN_RATE_LIMIT_FAIL_OPEN else "Refusing the attempt",
            exc_info=True,
        )
        if settings.LOGIN_RATE_LIMIT_FAIL_OPEN:
            return
        raise _unavailable() from None

    if not within_email_budget or not within_ip_budget:
        logger.warning(
            "Login rate limit reached (email budget: %s, ip budget: %s, ip: %s)",
            within_email_budget,
            within_ip_budget,
            ip,
        )
        raise _too_many()


def clear_login_rate_limit(email: str) -> None:
    """Forget the failed attempts for an account that just logged in successfully."""
    if not settings.LOGIN_RATE_LIMIT_ENABLED:
        return
    try:
        _client().delete(f"{_KEY_PREFIX}:email:{email.strip().lower()}")
    except redis.RedisError:
        # A counter that fails to clear expires on its own; never fail a good login here.
        logger.warning("Could not clear the login rate-limit counter", exc_info=True)
