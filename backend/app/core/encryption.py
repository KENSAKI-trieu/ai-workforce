
"""Encryption at rest for columns that hold contract text and chat content.

A contract review stores the whole contract twice (its text, and every clause in the
result), and a contract pasted into chat sits in the message table; all of it was plain
text in the database and in every backup of it.

Values are sealed with Fernet (AES-128-CBC + HMAC-SHA256) when DATA_ENCRYPTION_KEY is
set, and written as before when it is not. Reading never depends on the setting for a
plain value: rows written before the key existed stay readable, so turning encryption
on needs no migration (``python -m app.db.encrypt_existing_content`` seals them later).
DATA_ENCRYPTION_KEY may hold several comma-separated keys: the first seals, every one
opens, so a key can be rotated without losing rows sealed under the old one.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

SEALED_PREFIX = "enc:v1:"


class EncryptionKeyMissing(RuntimeError):
    """A sealed value was read with no key, or with none of the keys that sealed it."""


@lru_cache(maxsize=4)
def _cipher(keys: str) -> MultiFernet | None:
    parts = [part.strip() for part in keys.split(",") if part.strip()]
    return MultiFernet([Fernet(part.encode()) for part in parts]) if parts else None


def cipher() -> MultiFernet | None:
    return _cipher(settings.DATA_ENCRYPTION_KEY or "")


def is_sealed(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(SEALED_PREFIX)


def seal(value: str) -> str:
    """The value sealed with the current key, or unchanged when no key is set."""
    fernet = cipher()
    if fernet is None or is_sealed(value):
        return value
    return SEALED_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")


def unseal(value: str) -> str:
    if not is_sealed(value):
        return value
    fernet = cipher()
    if fernet is None:
        raise EncryptionKeyMissing("Encrypted data was read but DATA_ENCRYPTION_KEY is not set")
    try:
        return fernet.decrypt(value[len(SEALED_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionKeyMissing("No configured DATA_ENCRYPTION_KEY opens this value") from exc


class EncryptedText(TypeDecorator):
    """Text, sealed in the database."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        return None if value is None else seal(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        return None if value is None else unseal(value)


class EncryptedJSONB(TypeDecorator):
    """A JSON document stored as one sealed JSON string; plain documents still read."""

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None or cipher() is None:
            return value
        return seal(json.dumps(value, ensure_ascii=False))

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return json.loads(unseal(value)) if is_sealed(value) else value
