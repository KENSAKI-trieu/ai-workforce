"""Tenant-isolated local storage for generated legal approval artifacts."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path

from app.core.config import settings


def _root() -> Path:
    root = Path(settings.LEGAL_DRAFT_STORAGE_PATH)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:255] or "artifact"


def save_legal_artifact(
    *,
    tenant_id: uuid.UUID,
    artifact_id: str,
    variant: str,
    filename: str,
    content: bytes,
) -> str:
    root = _root()
    directory = (root / str(tenant_id) / _safe(artifact_id) / _safe(variant)).resolve()
    if root not in directory.parents:
        raise ValueError("Invalid legal artifact target")
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / _safe(Path(filename).name)).resolve()
    if directory not in target.parents:
        raise ValueError("Invalid legal artifact filename")
    descriptor, temporary_name = tempfile.mkstemp(dir=directory, prefix=".draft-")
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return target.relative_to(root).as_posix()


def read_legal_artifact(storage_key: str) -> bytes:
    root = _root()
    target = (root / storage_key).resolve()
    if root not in target.parents:
        raise ValueError("Invalid legal artifact key")
    return target.read_bytes()
