"""Local object-storage adapter for original knowledge files."""

import hashlib
import os
import re
import tempfile
import uuid
from pathlib import Path

from app.core.config import settings


def _storage_root() -> Path:
    root = Path(settings.KNOWLEDGE_STORAGE_PATH)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _safe_filename(filename: str) -> str:
    basename = Path(filename).name.strip() or "document"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", basename)[:255]


def save_original_file(
    *,
    tenant_id: uuid.UUID,
    document_id: str,
    version: str,
    filename: str,
    data: bytes,
) -> tuple[str, str]:
    """Persist bytes atomically and return a storage key plus SHA-256 hash."""
    root = _storage_root()
    relative_directory = Path(str(tenant_id)) / _safe_filename(document_id) / _safe_filename(version)
    target_directory = (root / relative_directory).resolve()
    if root != target_directory and root not in target_directory.parents:
        raise ValueError("Invalid knowledge storage target")
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / _safe_filename(filename)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=target_directory,
        prefix=".upload-",
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    storage_key = target.relative_to(root).as_posix()
    return storage_key, hashlib.sha256(data).hexdigest()


def read_original_file(storage_key: str) -> bytes:
    """Read a stored original while preventing paths from escaping the storage root."""
    root = _storage_root()
    target = (root / storage_key).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Invalid knowledge storage key")
    return target.read_bytes()


def delete_original_file(storage_key: str) -> bool:
    """Delete one stored original and prune its now-empty version/document folders."""
    root = _storage_root()
    target = (root / storage_key).resolve()
    if root == target or root not in target.parents:
        raise ValueError("Invalid knowledge storage key")
    if not target.exists():
        return False
    if not target.is_file():
        raise ValueError("Knowledge storage key does not point to a file")

    target.unlink()
    current = target.parent
    # A storage key normally has tenant/document/version/file. Only remove the
    # two private folders owned by this document version, never the tenant root.
    for _ in range(2):
        if current == root or root not in current.parents:
            break
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
    return True
