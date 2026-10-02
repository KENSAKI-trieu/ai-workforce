"""Sending one email under the workspace's delivery mode.

`outbox` (the default) records the message as accepted without sending anything, which
is what development and tests run on; `smtp` sends it. The same switch the support
workflow reads, so one setting decides whether this product emails anyone.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from app.core.config import settings


def deliver_email(recipient: str, subject: str, body: str) -> dict[str, Any]:
    """{"status": ACCEPTED|SENT, "provider_message_id", "sent_at"}; raises on a failed send."""
    mode = settings.EMAIL_DELIVERY_MODE.lower()
    now = datetime.now(timezone.utc).isoformat()
    if mode == "outbox":
        return {"status": "ACCEPTED", "provider_message_id": "outbox", "sent_at": now}
    if mode != "smtp":
        raise RuntimeError("EMAIL_DELIVERY_MODE must be 'outbox' or 'smtp'")
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        raise RuntimeError("SMTP_HOST and SMTP_FROM_EMAIL are required")
    email = EmailMessage()
    email["From"] = settings.SMTP_FROM_EMAIL
    email["To"] = recipient
    email["Subject"] = subject
    email.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as client:
        if settings.SMTP_USE_TLS:
            client.starttls()
        if settings.SMTP_USERNAME:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        client.send_message(email)
    return {"status": "SENT", "provider_message_id": email.get("Message-ID") or "smtp", "sent_at": now}
