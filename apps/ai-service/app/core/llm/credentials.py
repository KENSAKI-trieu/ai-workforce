"""API keys a company entered on its settings page, carried by the request that uses them.

The service stays stateless: the backend keeps each company's keys and sends them with
every call that reaches a model, in the X-LLM-Credentials header. A vendor named there
replaces this deployment's own keys for that vendor for the length of the request; a
vendor left out keeps them. Bedrock is never named: it authenticates the client, not the
request.

The header is base64url JSON, {"openai": ["sk-..."], "gemini": ["AIza..."]}. It is read
by `CredentialsMiddleware` into a context variable before any route runs, so the sync
routes, the streamed bodies and the graph built inside them all see the same keys.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

HEADER = "x-llm-credentials"
OVERRIDABLE_PROVIDERS = ("openai", "gemini")
MAX_KEYS_PER_PROVIDER = 4
MAX_KEY_LENGTH = 512

Overrides = dict[str, tuple[str, ...]]

_overrides: ContextVar[Overrides | None] = ContextVar("llm_credential_overrides", default=None)


def parse_header(value: str | None) -> Overrides | None:
    """The keys in a header value; None when it is absent, unreadable or names no key."""
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeError):
        # Never logged with the value: it is a credential.
        logger.warning("Ignoring an unreadable %s header", HEADER)
        return None
    if not isinstance(data, dict):
        return None
    result: Overrides = {}
    for provider in OVERRIDABLE_PROVIDERS:
        items = data.get(provider)
        if not isinstance(items, list):
            continue
        keys = tuple(dict.fromkeys(
            item.strip() for item in items
            if isinstance(item, str) and item.strip() and len(item) <= MAX_KEY_LENGTH
        ))[:MAX_KEYS_PER_PROVIDER]
        if keys:
            result[provider] = keys
    return result or None


def encode_header(overrides: dict[str, list[str] | tuple[str, ...]]) -> str:
    """The header value for these keys; the backend builds the same shape."""
    payload = json.dumps({key: list(value) for key, value in overrides.items()}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def overridden_keys(provider: str) -> tuple[str, ...] | None:
    """The request's keys for this vendor, or None when the deployment's own keys apply."""
    current = _overrides.get()
    if not current:
        return None
    return current.get(provider)


def fingerprint() -> str:
    """Tells cached results apart by the keys they were made with, without holding a key."""
    current = _overrides.get()
    if not current:
        return ""
    material = json.dumps(sorted((key, list(value)) for key, value in current.items()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@contextmanager
def using_credentials(overrides: Overrides | None) -> Iterator[None]:
    token = _overrides.set(overrides or None)
    try:
        yield
    finally:
        _overrides.reset(token)


class CredentialsMiddleware:
    """Binds the request's X-LLM-Credentials for everything that runs under it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = None
        for name, value in scope.get("headers") or []:
            if name.decode("latin-1").lower() == HEADER:
                raw = value.decode("latin-1")
                break
        with using_credentials(parse_header(raw)):
            await self.app(scope, receive, send)
