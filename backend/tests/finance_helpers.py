"""Shared set-up for the Finance tests: people holding chosen boxes, and Excel files."""

from __future__ import annotations

import io
import uuid
from typing import Any, Iterable

from openpyxl import Workbook

from app.core.security import get_password_hash
from app.domains.platform.position_service import assign_position
from app.models.models import Position, User


def person(db, *codes: str, department: str | None = None) -> User:
    """A user of the seeded tenant on a fresh position granting exactly `codes`."""
    anchor = db.query(User).filter(User.email == "employee@company.com").one()
    position = Position(
        id=uuid.uuid4(), tenant_id=anchor.tenant_id, parent_id=anchor.position.parent_id,
        name="Vị trí thử Tài chính", slug=f"test-fin-{uuid.uuid4().hex[:8]}",
        permissions=sorted(codes), grants_all=False, sort_order=999, is_active=True,
    )
    db.add(position)
    user = User(
        id=uuid.uuid4(), tenant_id=anchor.tenant_id, email=f"fin-{uuid.uuid4().hex[:8]}@company.com",
        full_name="Người Thử Tài Chính", password_hash=get_password_hash("Password123!"),
        role="Employee", department=department or anchor.department, is_active=True,
    )
    db.add(user)
    db.flush()
    assign_position(db, user, position)
    db.commit()
    return user


def login(client, user: User) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": user.email, "password": "Password123!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def xlsx(header: Iterable[str], rows: Iterable[Iterable[Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(header))
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def upload(client, headers, kind: str, data: bytes):
    return client.post(
        f"/api/v1/finance/import/{kind}",
        files={"file": (f"{kind}.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )


def unique_tax_code() -> str:
    return str(uuid.uuid4().int)[:10]
