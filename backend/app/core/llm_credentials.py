"""A company's own model API keys: sealed in the database, sent with its model calls.

An administrator enters a key per vendor on the settings page. From then on every model
call the company makes -- chat, the graph, contract review, marketing, web search, the
model picker -- runs on that key instead of the deployment's own for that vendor. A
vendor without a company key keeps the deployment's keys, as before. Embeddings are left
out on purpose: the stored vectors were made by the deployment's embedding model, and a
different key could mean a different model and vectors that no longer compare.

How a call finds the company. The AI service client is reached from dozens of modules
that know nothing about tenants, so the tenant travels in a context variable, bound once
per request by `TenantContextMiddleware` from the bearer token (a user's access token, or
the internal token the graph presents at the tool gateway). Thread pools that fan out
model calls already run in a copy of the caller's context; the two plain threads that run
streamed steps do too. Code outside a request binds it with `using_tenant`.

Sealing. Keys are sealed with DATA_ENCRYPTION_KEY when it is set, and otherwise with a
key derived from SECRET_KEY, so a key is never stored in the clear. Every configured key
opens, so turning DATA_ENCRYPTION_KEY on later keeps the stored keys readable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings

logger = logging.getLogger(__name__)

HEADER = "X-LLM-Credentials"
# Vendors whose keys a company can enter: the ones the AI service calls with an API key.
PROVIDER_LABELS: dict[str, str] = {"openai": "OpenAI", "gemini": "Google Gemini"}
CACHE_TTL_SECONDS = 60.0

_tenant: ContextVar[str | None] = ContextVar("llm_credentials_tenant", default=None)


class CredentialUnreadable(RuntimeError):
    """A stored key no configured secret opens (SECRET_KEY changed, for instance)."""


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4)
def _cipher(data_keys: str, secret_key: str) -> MultiFernet:
    fernets = [Fernet(part.strip().encode()) for part in data_keys.split(",") if part.strip()]
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(b"ai-workforce:llm-credentials:" + secret_key.encode("utf-8")).digest()
    )
    fernets.append(Fernet(derived))
    return MultiFernet(fernets)


def _current_cipher() -> MultiFernet:
    return _cipher(settings.DATA_ENCRYPTION_KEY or "", settings.SECRET_KEY)


def seal_key(api_key: str) -> str:
    return _current_cipher().encrypt(api_key.encode("utf-8")).decode("ascii")


def open_key(sealed: str) -> str:
    try:
        return _current_cipher().decrypt(sealed.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialUnreadable("No configured secret opens this stored API key") from exc


def key_hint(api_key: str) -> str:
    """What the page shows of a key: its last four characters."""
    return f"…{api_key[-4:]}" if len(api_key) > 8 else "…"


# ---------------------------------------------------------------------------
# The tenant a model call is made for
# ---------------------------------------------------------------------------
def current_tenant() -> str | None:
    return _tenant.get()


@contextmanager
def using_tenant(tenant_id: uuid.UUID | str | None) -> Iterator[None]:
    token = _tenant.set(str(tenant_id) if tenant_id else None)
    try:
        yield
    finally:
        _tenant.reset(token)


def _tenant_from_authorization(value: str | None) -> str | None:
    """The tenant a verified bearer token names; None for anything else.

    Only the signature is checked: deciding whether the caller may do anything is still
    each endpoint's job. A forged token names no tenant here, so it can only ever reach
    the deployment's own keys, and the endpoint rejects it regardless.
    """
    from fastapi import HTTPException

    from app.core.security import decode_internal_tool_token, decode_token

    if not value or not value.lower().startswith("bearer "):
        return None
    token = value[7:].strip()
    for decode in (decode_token, decode_internal_tool_token):
        try:
            tenant_id = decode(token).get("tenant_id")
        except HTTPException:
            continue
        if tenant_id:
            return str(tenant_id)
    return None


class TenantContextMiddleware:
    """Binds the caller's tenant for the whole request, streamed body included."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        authorization = None
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                authorization = value.decode("latin-1")
                break
        with using_tenant(_tenant_from_authorization(authorization)):
            await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Looking the keys up
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[float, dict[str, list[str]]]] = {}
_cache_lock = threading.Lock()


def _load_keys(tenant_id: str) -> dict[str, list[str]]:
    from app.core.database import SyncSessionLocal
    from app.models.models import TenantLLMCredential

    db = SyncSessionLocal()
    try:
        rows = (
            db.query(TenantLLMCredential.provider, TenantLLMCredential.sealed_key)
            .filter(TenantLLMCredential.tenant_id == uuid.UUID(tenant_id))
            .all()
        )
    finally:
        db.close()
    keys: dict[str, list[str]] = {}
    for provider, sealed in rows:
        if provider not in PROVIDER_LABELS:
            continue
        try:
            keys[provider] = [open_key(sealed)]
        except CredentialUnreadable:
            # The deployment's key runs instead; the settings page shows the key as unreadable.
            logger.warning("Stored %s key of tenant %s cannot be opened", provider, tenant_id)
    return keys


def tenant_keys(tenant_id: str) -> dict[str, list[str]]:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(tenant_id)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    keys = _load_keys(tenant_id)
    with _cache_lock:
        _cache[tenant_id] = (now, keys)
    return keys


def forget_tenant(tenant_id: uuid.UUID | str) -> None:
    """Drops the cached keys after a change. Other worker processes catch up within a minute."""
    with _cache_lock:
        _cache.pop(str(tenant_id), None)


def encode_header(keys: dict[str, list[str]]) -> str:
    payload = json.dumps(keys).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def request_headers() -> dict[str, str]:
    """The header carrying the bound company's keys to the AI service, if it has any."""
    tenant_id = current_tenant()
    if not tenant_id:
        return {}
    try:
        keys = tenant_keys(tenant_id)
    except Exception:  # the database being unreachable must not fail the model call itself
        logger.exception("Could not read the API keys of tenant %s", tenant_id)
        return {}
    return {HEADER: encode_header(keys)} if keys else {}
