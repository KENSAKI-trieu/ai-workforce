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


def delete_legal_artifact(storage_key: str) -> None:
    """Remove a stored artifact, and the folders it leaves empty. A missing file is no error."""
    root = _root()
    target = (root / storage_key).resolve()
    if root not in target.parents:
        raise ValueError("Invalid legal artifact key")
    target.unlink(missing_ok=True)
    # <tenant>/<artifact>/<variant>/<file>: the variant and artifact folders go when empty,
    # the tenant folder stays.
    for folder in list(target.parents)[:2]:
        if root not in folder.parents or folder.parent == root:
            break
        try:
            folder.rmdir()
        except OSError:  # not empty: another variant of the same artifact is still there
            break
