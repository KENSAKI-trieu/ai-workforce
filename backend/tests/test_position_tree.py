"""Phase 1 of the company-defined org tree: seeding, permission resolution, safety rails.

The safety rails get the most coverage on purpose. A tenant has no super-user outside
itself, so a change that removes its last administrator, or that silently revokes document
access, has no path back.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.core.permissions import (
    DEFAULT_POSITIONS,
    LEGACY_ROLE_TO_SLUG,
    PERMISSION_CODES,
    ROOT_POSITION_SLUG,
    normalize_permissions,
    permission_catalog,
)
from app.models.models import Position, Tenant, User
from app.domains.platform.position_service import (
    assert_no_cycle,
    assert_no_privilege_escalation,
    assert_not_last_administrator,
    assign_position,
    backfill_tenant_user_positions,
    derive_manager_id,
    ensure_tenant_positions,
    position_permissions,
    unique_slug,
    user_permissions,
)


def _tenant(db) -> Tenant:
    tenant = Tenant(
        id=uuid.uuid4(),
        name=f"Cty {uuid.uuid4().hex[:8]}",
        domain=f"cty-{uuid.uuid4().hex[:8]}.test",
    )
    db.add(tenant)
    db.flush()
    return tenant


def _user(db, tenant, *, role="Employee", email=None, active=True) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=email or f"{uuid.uuid4().hex[:10]}@cty.test",
        full_name="Người Thử",
        password_hash="x",
        role=role,
        department="ALL",
        is_active=active,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------- vocabulary


def test_permission_catalog_is_complete_and_unique():
    catalog = permission_catalog()
    codes = [item["code"] for item in catalog]
    assert len(codes) == len(set(codes)), "trùng mã quyền"
    assert set(codes) == set(PERMISSION_CODES)
    assert all(item["label"] and item["group"] and item["description"] for item in catalog)


def test_normalize_permissions_drops_unknown_codes_instead_of_failing():
    """A row written by a newer build must not break an older one."""
    result = normalize_permissions(["agents.configure", "does.not.exist", "agents.configure"])
    assert result == ["agents.configure"]


def test_default_tree_declares_parents_before_children():
    seen: set[str] = set()
    for item in DEFAULT_POSITIONS:
        if item.parent_slug is not None:
            assert item.parent_slug in seen, f"{item.slug} đứng trước cha của nó"
        seen.add(item.slug)


def test_default_slugs_match_the_legacy_role_strings_document_acls_store():
    """Document ACLs persist role strings lowercased; the slugs must still match them."""
    for legacy_role, slug in LEGACY_ROLE_TO_SLUG.items():
        assert slug == legacy_role.lower()


# ---------------------------------------------------------------- seeding


def test_seeding_is_idempotent(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)

    first = ensure_tenant_positions(db, tenant.id)
    second = ensure_tenant_positions(db, tenant.id)

    assert set(first) == set(second) == {item.slug for item in DEFAULT_POSITIONS}
    assert {p.id for p in first.values()} == {p.id for p in second.values()}
    assert db.query(Position).filter(Position.tenant_id == tenant.id).count() == len(
        DEFAULT_POSITIONS
    )


def test_backfill_places_every_user_and_repeats_cleanly(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    owner = _user(db, tenant, role="Owner")
    manager = _user(db, tenant, role="Manager")
    employee = _user(db, tenant, role="Employee")
    stranger = _user(db, tenant, role="KhôngCóTrongDanhSách")

    assigned = backfill_tenant_user_positions(db, tenant.id)
    assert assigned == 4

    positions = {p.id: p for p in db.query(Position).filter(Position.tenant_id == tenant.id)}
    assert positions[owner.position_id].slug == "owner"
    assert positions[manager.position_id].slug == "manager"
    assert positions[employee.position_id].slug == "employee"
    # An unrecognised legacy role lands on the least-privileged node, never nowhere.
    assert positions[stranger.position_id].slug == "employee"

    assert backfill_tenant_user_positions(db, tenant.id) == 0
    db.refresh(owner)
    assert positions[owner.position_id].slug == "owner"


def test_the_root_grants_every_permission_including_ones_added_later(
    transactional_db_session,
):
    """grants_all is what stops a new permission code from locking a company out."""
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    root = positions[ROOT_POSITION_SLUG]

    assert root.grants_all is True
    assert root.permissions == []
    assert position_permissions(root) == PERMISSION_CODES


def test_permissions_of_a_user_without_a_position_are_empty(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    user = _user(db, tenant)

    assert user_permissions(db, user) == frozenset()


def test_an_inactive_position_grants_nothing(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    admin = positions["admin"]
    assert position_permissions(admin)

    admin.is_active = False
    assert position_permissions(admin) == frozenset()


# ---------------------------------------------------------------- rename safety


def test_renaming_a_position_changes_no_permission_and_no_slug(transactional_db_session):
    """The regression the whole design exists to prevent."""
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    manager = positions["manager"]
    before = position_permissions(manager)
    slug_before = manager.slug

    manager.name = "Trưởng bộ phận Kinh doanh Miền Nam"
    db.flush()

    assert manager.slug == slug_before
    assert position_permissions(manager) == before


def test_unique_slug_never_collides_within_a_tenant(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    ensure_tenant_positions(db, tenant.id)

    first = unique_slug(db, tenant.id, "Quản lý")
    assert first != "manager"

    db.add(Position(
        id=uuid.uuid4(), tenant_id=tenant.id, name="Quản lý", slug=first,
        permissions=[], sort_order=99,
    ))
    db.flush()
    assert unique_slug(db, tenant.id, "Quản lý") not in {"manager", first}


def test_slug_is_ascii_even_for_vietnamese_names(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)

    slug = unique_slug(db, tenant.id, "Trưởng phòng Kỹ thuật Đà Nẵng")

    assert slug == "truong-phong-ky-thuat-da-nang"


# ---------------------------------------------------------------- cycles


def test_a_position_cannot_become_its_own_parent(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)

    with pytest.raises(HTTPException) as denied:
        assert_no_cycle(db, positions["manager"], positions["manager"])
    assert denied.value.status_code == 422


def test_a_position_cannot_be_reparented_under_its_own_descendant(
    transactional_db_session,
):
    """Three levels deep: ceo -> manager -> employee, then try ceo under employee."""
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)

    with pytest.raises(HTTPException) as denied:
        assert_no_cycle(db, positions["ceo"], positions["employee"])
    assert denied.value.status_code == 422


def test_reparenting_to_an_unrelated_branch_is_allowed(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)

    assert_no_cycle(db, positions["employee"], positions["admin"])


def test_a_position_from_another_tenant_is_not_a_valid_parent(transactional_db_session):
    db = transactional_db_session
    mine = ensure_tenant_positions(db, _tenant(db).id)
    theirs = ensure_tenant_positions(db, _tenant(db).id)

    with pytest.raises(HTTPException) as denied:
        assert_no_cycle(db, mine["manager"], theirs["admin"])
    assert denied.value.status_code == 404


# ---------------------------------------------------------------- escalation


def test_nobody_can_grant_a_permission_they_do_not_hold(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    manager_user = _user(db, tenant, role="Manager")
    assign_position(db, manager_user, positions["manager"])

    with pytest.raises(HTTPException) as denied:
        assert_no_privilege_escalation(db, manager_user, ["workspace.delete"])
    assert denied.value.status_code == 403
    assert "workspace.delete" in denied.value.detail


def test_granting_a_subset_of_your_own_permissions_is_allowed(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    root_user = _user(db, tenant, role="Owner")
    assign_position(db, root_user, positions[ROOT_POSITION_SLUG])

    granted = assert_no_privilege_escalation(
        db, root_user, ["workspace.delete", "agents.configure"]
    )

    assert granted == ["agents.configure", "workspace.delete"]


# ---------------------------------------------------------------- lockout


def test_removing_the_last_administrator_is_refused(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    sole_admin = _user(db, tenant, role="Owner")
    assign_position(db, sole_admin, positions[ROOT_POSITION_SLUG])
    db.flush()

    assert_not_last_administrator(db, tenant.id)

    with pytest.raises(HTTPException) as denied:
        assert_not_last_administrator(db, tenant.id, ignore_user_id=sole_admin.id)
    assert denied.value.status_code == 422


def test_stripping_the_last_administrator_position_is_refused(transactional_db_session):
    """Checked against the proposed permission set, before anything is written."""
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    ceo_user = _user(db, tenant, role="CEO")
    assign_position(db, ceo_user, positions["ceo"])
    db.flush()

    with pytest.raises(HTTPException):
        assert_not_last_administrator(
            db,
            tenant.id,
            ignore_position_id=positions["ceo"].id,
            replacement_permissions=frozenset({"users.view"}),
        )


def test_a_second_administrator_makes_the_change_safe(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    first = _user(db, tenant, role="CEO")
    second = _user(db, tenant, role="Owner")
    assign_position(db, first, positions["ceo"])
    assign_position(db, second, positions[ROOT_POSITION_SLUG])
    db.flush()

    assert_not_last_administrator(db, tenant.id, ignore_user_id=first.id)


def test_a_locked_administrator_does_not_count(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    active_admin = _user(db, tenant, role="Owner")
    locked_admin = _user(db, tenant, role="Owner", active=False)
    assign_position(db, active_admin, positions[ROOT_POSITION_SLUG])
    assign_position(db, locked_admin, positions[ROOT_POSITION_SLUG])
    db.flush()

    with pytest.raises(HTTPException):
        assert_not_last_administrator(db, tenant.id, ignore_user_id=active_admin.id)


# ---------------------------------------------------------------- manager derivation


def test_assigning_a_position_derives_the_manager_from_the_tree(
    transactional_db_session,
):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    boss = _user(db, tenant, role="Manager")
    assign_position(db, boss, positions["manager"])
    db.flush()

    report = _user(db, tenant, role="Employee")
    manager_id = assign_position(db, report, positions["employee"])

    assert manager_id == boss.id
    assert report.manager_id == boss.id


def test_manager_derivation_skips_a_vacant_level(transactional_db_session):
    """employee -> manager (vacant) -> ceo (held): the held ancestor wins."""
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    chief = _user(db, tenant, role="CEO")
    assign_position(db, chief, positions["ceo"])
    db.flush()

    report = _user(db, tenant, role="Employee")
    assign_position(db, report, positions["employee"])

    assert report.manager_id == chief.id


def test_a_manually_set_manager_survives_a_position_change(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    tree_boss = _user(db, tenant, role="Manager")
    chosen_boss = _user(db, tenant, role="Manager")
    assign_position(db, tree_boss, positions["manager"])
    db.flush()

    report = _user(db, tenant, role="Employee")
    report.manager_id = chosen_boss.id
    report.manager_is_manual = True

    assign_position(db, report, positions["employee"])

    assert report.manager_id == chosen_boss.id


def test_the_root_position_derives_no_manager(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    founder = _user(db, tenant, role="Owner")

    assert derive_manager_id(db, founder, positions[ROOT_POSITION_SLUG]) is None


def test_a_user_is_never_made_their_own_manager(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    solo = _user(db, tenant, role="Manager")
    assign_position(db, solo, positions["manager"])
    db.flush()

    assign_position(db, solo, positions["employee"])

    assert solo.manager_id != solo.id


def test_assigning_a_position_from_another_tenant_is_refused(transactional_db_session):
    db = transactional_db_session
    tenant = _tenant(db)
    other = ensure_tenant_positions(db, _tenant(db).id)
    user = _user(db, tenant)

    with pytest.raises(HTTPException) as denied:
        assign_position(db, user, other["manager"])
    assert denied.value.status_code == 404


# ---------------------------------------------------------------- registration


def test_registering_a_company_puts_the_founder_on_the_root_position(
    client, transactional_db_session
):
    """The founder must land somewhere that cannot be locked out of its own company."""
    email = f"founder-{uuid.uuid4().hex[:8]}@newco-example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Password123!",
            "full_name": "Người Sáng Lập",
            "tenant_name": f"NewCo {uuid.uuid4().hex[:6]}",
        },
    )
    assert response.status_code in (200, 201), response.text

    founder = transactional_db_session.query(User).filter(User.email == email).one()
    assert founder.position_id is not None
    assert founder.position.slug == ROOT_POSITION_SLUG
    assert founder.position.grants_all is True
    # Displayed as CEO, which is what the person creating the company expects to be called.
    assert founder.position.name == "CEO"
    assert user_permissions(transactional_db_session, founder) == PERMISSION_CODES

    tenant_positions = transactional_db_session.query(Position).filter(
        Position.tenant_id == founder.tenant_id
    ).all()
    assert len(tenant_positions) == len(DEFAULT_POSITIONS)
    assert_not_last_administrator(transactional_db_session, founder.tenant_id)


# ---------------------------------------------------------------- legacy role sync


def test_assigning_a_position_rewrites_the_legacy_role_string(transactional_db_session):
    """Guards that still branch on `User.role` must not disagree with the org chart."""
    db = transactional_db_session
    tenant = _tenant(db)
    positions = ensure_tenant_positions(db, tenant.id)
    person = _user(db, tenant, role="Employee")

    assign_position(db, person, positions["manager"])
    assert person.role == "Manager"

    assign_position(db, person, positions["admin"])
    assert person.role == "Admin"

    # The root is what the founder holds, and it is called CEO -- never "Owner".
    assign_position(db, person, positions[ROOT_POSITION_SLUG])
    assert person.role == "CEO"

    assign_position(db, person, positions["employee"])
    assert person.role == "Employee"


def test_a_company_defined_position_gets_a_role_matching_its_powers(
    transactional_db_session,
):
    """A custom position has no legacy twin, so it is classified by what it grants."""
    db = transactional_db_session
    tenant = _tenant(db)
    ensure_tenant_positions(db, tenant.id)

    def _custom(name: str, permissions: list[str]) -> Position:
        position = Position(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name=name,
            slug=unique_slug(db, tenant.id, name),
            permissions=normalize_permissions(permissions),
            grants_all=False,
        )
        db.add(position)
        db.flush()
        return position

    person = _user(db, tenant)

    assign_position(db, person, _custom("Trưởng phòng Vận hành", ["approvals.sign"]))
    assert person.role == "Manager"

    assign_position(db, person, _custom("Giám sát hệ thống", ["users.manage"]))
    assert person.role == "Admin"

    # Read-only crumbs an ordinary member of staff also holds must not inflate the role.
    assign_position(db, person, _custom("Thực tập sinh", ["hr.directory.view"]))
    assert person.role == "Employee"
