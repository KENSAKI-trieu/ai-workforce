"""The org-structure API: a company building and renaming its own position tree.

The rename test is the one that matters most. Document ACLs store role strings as data, so
a rename that changed the stored identity would silently revoke people's access to
documents with no error anywhere.
"""
from __future__ import annotations

import uuid

import pytest

from app.domains.platform.position_service import ensure_tenant_positions
from app.models.models import DocumentChunk, Position, Tenant, User


def _login(client, email: str, password: str = "Password123!") -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def ceo_headers(client, ceo_token_headers):
    return ceo_token_headers


def _tree(client, headers) -> dict:
    response = client.get("/api/v1/positions", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _by_slug(payload: dict, slug: str) -> dict:
    return next(item for item in payload["positions"] if item["slug"] == slug)


# ---------------------------------------------------------------- reading


def test_the_tree_is_seeded_and_readable(client, ceo_headers):
    payload = _tree(client, ceo_headers)

    slugs = {item["slug"] for item in payload["positions"]}
    assert {"owner", "ceo", "admin", "manager", "employee", "guest"} <= slugs
    root = _by_slug(payload, "owner")
    assert root["parent_id"] is None
    assert root["grants_all"] is True
    assert root["name"] == "CEO"


def test_the_permission_catalog_is_available_for_the_picker(client, ceo_headers):
    response = client.get("/api/v1/positions/permissions", headers=ceo_headers)

    assert response.status_code == 200
    permissions = response.json()["permissions"]
    codes = {item["code"] for item in permissions}
    assert "org.structure.manage" in codes
    assert all(item["label"] and item["group"] for item in permissions)


def test_an_ordinary_employee_can_read_the_org_chart(client, employee_token_headers):
    """An org chart is not privileged information; changing it is."""
    payload = _tree(client, employee_token_headers)

    assert payload["positions"]
    assert payload["my_permissions"] == []


# ---------------------------------------------------------------- creating


def test_creating_a_position_with_a_vietnamese_name(client, ceo_headers):
    response = client.post(
        "/api/v1/positions",
        headers=ceo_headers,
        json={
            "name": "Trưởng phòng Kinh doanh Miền Nam",
            "parent_id": _by_slug(_tree(client, ceo_headers), "ceo")["id"],
            "permissions": ["users.view", "hr.directory.view"],
        },
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "Trưởng phòng Kinh doanh Miền Nam"
    assert created["slug"] == "truong-phong-kinh-doanh-mien-nam"
    assert created["permissions"] == ["hr.directory.view", "users.view"]
    assert created["holder_count"] == 0


def test_creating_a_position_cannot_grant_what_you_lack(client, ceo_token_headers):
    """The CEO node deliberately lacks workspace.delete, so it cannot hand it out."""
    response = client.post(
        "/api/v1/positions",
        headers=ceo_token_headers,
        json={"name": "Siêu quản trị", "permissions": ["workspace.delete"]},
    )

    assert response.status_code == 403, response.text
    assert "workspace.delete" in response.json()["detail"]


def test_an_employee_cannot_create_a_position(client, employee_token_headers):
    response = client.post(
        "/api/v1/positions",
        headers=employee_token_headers,
        json={"name": "Tự phong", "permissions": []},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------- renaming


def test_renaming_a_position_keeps_its_slug_and_document_access(
    client, ceo_headers, transactional_db_session
):
    """The regression this whole feature is designed around."""
    db = transactional_db_session
    actor = db.query(User).filter(User.email == "admin@company.com").one()
    chunk = db.query(DocumentChunk).filter(
        DocumentChunk.tenant_id == actor.tenant_id
    ).first()
    assert chunk is not None, "cần ít nhất một chunk trong dữ liệu seed"
    chunk.allowed_roles = ["manager"]
    db.commit()

    manager = _by_slug(_tree(client, ceo_headers), "manager")
    response = client.patch(
        f"/api/v1/positions/{manager['id']}",
        headers=ceo_headers,
        json={"name": "Trưởng bộ phận"},
    )
    assert response.status_code == 200, response.text
    renamed = response.json()

    assert renamed["name"] == "Trưởng bộ phận"
    # The stored identity is untouched, so the ACL row still points at the same position.
    assert renamed["slug"] == "manager"
    assert renamed["permissions"] == manager["permissions"]

    db.refresh(chunk)
    assert chunk.allowed_roles == ["manager"]


def test_the_root_position_can_be_renamed_but_not_stripped(client, ceo_token_headers):
    root = _by_slug(_tree(client, ceo_token_headers), "owner")

    renamed = client.patch(
        f"/api/v1/positions/{root['id']}",
        headers=ceo_token_headers,
        json={"name": "Tổng giám đốc"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["grants_all"] is True

    stripped = client.patch(
        f"/api/v1/positions/{root['id']}",
        headers=ceo_token_headers,
        json={"permissions": []},
    )
    assert stripped.status_code == 422
    assert "gốc" in stripped.json()["detail"]


# ---------------------------------------------------------------- restructuring


def test_a_position_cannot_be_moved_under_its_own_descendant(client, ceo_headers):
    payload = _tree(client, ceo_headers)
    ceo = _by_slug(payload, "ceo")
    employee = _by_slug(payload, "employee")

    response = client.patch(
        f"/api/v1/positions/{ceo['id']}",
        headers=ceo_headers,
        json={"parent_id": employee["id"]},
    )

    assert response.status_code == 422
    assert "dưới chức vụ này" in response.json()["detail"]


def test_moving_a_position_to_an_unrelated_branch_works(client, ceo_headers):
    payload = _tree(client, ceo_headers)
    guest = _by_slug(payload, "guest")
    admin = _by_slug(payload, "admin")

    response = client.patch(
        f"/api/v1/positions/{guest['id']}",
        headers=ceo_headers,
        json={"parent_id": admin["id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["parent_id"] == admin["id"]


# ---------------------------------------------------------------- deleting


def test_a_position_with_children_cannot_be_deleted(client, ceo_headers):
    manager = _by_slug(_tree(client, ceo_headers), "manager")

    response = client.delete(
        f"/api/v1/positions/{manager['id']}", headers=ceo_headers
    )

    assert response.status_code == 422
    assert "cấp dưới" in response.json()["detail"]


def test_a_position_with_holders_requires_a_replacement(client, ceo_headers):
    payload = _tree(client, ceo_headers)
    employee = _by_slug(payload, "employee")
    assert employee["holder_count"] >= 1

    refused = client.delete(
        f"/api/v1/positions/{employee['id']}", headers=ceo_headers
    )
    assert refused.status_code == 422
    assert "reassign_to" in refused.json()["detail"]

    guest = _by_slug(payload, "guest")
    moved = client.delete(
        f"/api/v1/positions/{employee['id']}?reassign_to={guest['id']}",
        headers=ceo_headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["reassigned"] == employee["holder_count"]


def test_the_root_position_cannot_be_deleted(client, ceo_headers):
    root = _by_slug(_tree(client, ceo_headers), "owner")

    response = client.delete(f"/api/v1/positions/{root['id']}", headers=ceo_headers)

    assert response.status_code == 422
    assert "gốc" in response.json()["detail"]


# ---------------------------------------------------------------- assigning


def test_assigning_a_position_derives_the_manager(
    client, ceo_headers, transactional_db_session
):
    db = transactional_db_session
    payload = _tree(client, ceo_headers)
    employee = db.query(User).filter(User.email == "employee@company.com").one()
    manager_position = _by_slug(payload, "manager")
    manager_holder = db.query(User).filter(
        User.tenant_id == employee.tenant_id,
        User.position_id == uuid.UUID(manager_position["id"]),
        User.is_active.is_(True),
    ).order_by(User.created_at).first()
    assert manager_holder is not None

    employee.manager_is_manual = False
    db.commit()

    response = client.put(
        f"/api/v1/positions/assign/{employee.id}",
        headers=ceo_headers,
        json={"position_id": _by_slug(payload, "employee")["id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["manager_id"] == str(manager_holder.id)


def test_an_explicit_manager_is_kept_and_marked_manual(
    client, ceo_headers, transactional_db_session
):
    db = transactional_db_session
    payload = _tree(client, ceo_headers)
    employee = db.query(User).filter(User.email == "employee@company.com").one()
    chosen = db.query(User).filter(User.email == "admin@company.com").one()

    response = client.put(
        f"/api/v1/positions/assign/{employee.id}",
        headers=ceo_headers,
        json={
            "position_id": _by_slug(payload, "employee")["id"],
            "manager_id": str(chosen.id),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["manager_id"] == str(chosen.id)
    assert body["manager_is_manual"] is True


def test_nobody_can_be_made_their_own_manager(
    client, ceo_headers, transactional_db_session
):
    db = transactional_db_session
    payload = _tree(client, ceo_headers)
    employee = db.query(User).filter(User.email == "employee@company.com").one()

    response = client.put(
        f"/api/v1/positions/assign/{employee.id}",
        headers=ceo_headers,
        json={
            "position_id": _by_slug(payload, "employee")["id"],
            "manager_id": str(employee.id),
        },
    )

    assert response.status_code == 422


def test_assigning_cannot_hand_out_permissions_the_actor_lacks(
    client, ceo_token_headers, transactional_db_session
):
    """The CEO node lacks workspace.delete, so it cannot promote anyone to the root."""
    db = transactional_db_session
    payload = _tree(client, ceo_token_headers)
    employee = db.query(User).filter(User.email == "employee@company.com").one()

    response = client.put(
        f"/api/v1/positions/assign/{employee.id}",
        headers=ceo_token_headers,
        json={"position_id": _by_slug(payload, "owner")["id"]},
    )

    assert response.status_code == 403
    assert "workspace.delete" in response.json()["detail"]


def test_holders_endpoint_lists_the_people_in_a_position(client, ceo_headers):
    employee = _by_slug(_tree(client, ceo_headers), "employee")

    response = client.get(
        f"/api/v1/positions/{employee['id']}/holders", headers=ceo_headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["position_id"] == employee["id"]
    assert len(body["holders"]) == employee["holder_count"]


# ---------------------------------------------------------------- tenant isolation


def test_a_position_of_another_tenant_is_invisible(
    client, ceo_headers, transactional_db_session
):
    db = transactional_db_session
    # A tenant of its own rather than whatever else the database holds: CI seeds a single
    # tenant, so a lookup for "some other tenant's position" found nothing there.
    other = Tenant(id=uuid.uuid4(), name="Công ty khác", domain=f"{uuid.uuid4().hex[:10]}.test")
    db.add(other)
    db.flush()
    foreign = ensure_tenant_positions(db, other.id)["employee"]

    read = client.get(f"/api/v1/positions/{foreign.id}/holders", headers=ceo_headers)
    assert read.status_code == 404

    write = client.patch(
        f"/api/v1/positions/{foreign.id}", headers=ceo_headers, json={"name": "Chiếm"}
    )
    assert write.status_code == 404
