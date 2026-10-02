"""Tenant-isolated storage for uploaded invoice files, sealed like uploaded contracts."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.encryption import seal_bytes, unseal_bytes


def _root() -> Path:
    root = Path(settings.FINANCE_STORAGE_PATH)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:255] or "file"


def save_invoice_file(*, tenant_id: uuid.UUID, invoice_id: uuid.UUID, filename: str, data: bytes) -> str:
    root = _root()
    directory = (root / str(tenant_id) / "invoices" / str(invoice_id)).resolve()
    if root not in directory.parents:
        raise ValueError("Invalid finance storage target")
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / _safe(Path(filename).name)).resolve()
    if directory not in target.parents:
        raise ValueError("Invalid finance file name")
    descriptor, temporary_name = tempfile.mkstemp(dir=directory, prefix=".upload-")
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(seal_bytes(data))
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


def read_invoice_file(storage_key: str) -> bytes:
    root = _root()
    target = (root / storage_key).resolve()
    if root not in target.parents:
        raise ValueError("Invalid finance storage key")
    return unseal_bytes(target.read_bytes())
