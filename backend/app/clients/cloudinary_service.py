"""Minimal signed Cloudinary image upload without a vendor SDK dependency."""

from __future__ import annotations

import hashlib
import time
from urllib.parse import unquote, urlparse

import httpx
from fastapi import HTTPException

from app.core.config import settings


def _credentials() -> tuple[str, str, str]:
    if not settings.CLOUDINARY_URL:
        raise HTTPException(status_code=503, detail="Avatar storage is not configured")
    parsed = urlparse(settings.CLOUDINARY_URL)
    if (
        parsed.scheme != "cloudinary"
        or not parsed.username
        or not parsed.password
        or not parsed.hostname
    ):
        raise HTTPException(status_code=503, detail="Avatar storage configuration is invalid")
    return unquote(parsed.hostname), unquote(parsed.username), unquote(parsed.password)


def upload_avatar(content: bytes, *, tenant_id: str, user_id: str, content_type: str) -> str:
    cloud_name, api_key, api_secret = _credentials()
    timestamp = int(time.time())
    folder = "ai-workforce/avatars"
    public_id = f"{tenant_id}/{user_id}"
    signed_parameters = {
        "folder": folder,
        "overwrite": "true",
        "public_id": public_id,
        "timestamp": str(timestamp),
    }
    signature_base = "&".join(
        f"{key}={value}" for key, value in sorted(signed_parameters.items())
    )
    signature = hashlib.sha1(
        f"{signature_base}{api_secret}".encode("utf-8")
    ).hexdigest()

    try:
        response = httpx.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
            data={
                **signed_parameters,
                "api_key": api_key,
                "signature": signature,
            },
            files={"file": ("avatar", content, content_type)},
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Avatar upload failed") from exc

    secure_url = response.json().get("secure_url")
    if not secure_url:
        raise HTTPException(status_code=502, detail="Avatar provider returned no URL")
    return str(secure_url)
