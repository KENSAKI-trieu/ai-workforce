"""The HR directory must answer the question that was asked.

Routing used to be coarser than the tools underneath it: a request for one department
listed the whole company, a request for the management left out the founder, and asking
for an employee by name or number was not understood at all. These pin the narrower
behaviour, including the part that matters most -- a filter never widens what a person is
allowed to see.
"""

import pytest

from app.models.models import Tenant, User
from app.services.agents.agent_executor import (
    _classify_hr_intent,
    _extract_employee_search_term,
    _resolve_requested_departments,
)
from app.domains.platform.position_service import supervisory_role_names


def _chat(client, headers, message: str) -> dict:
    response = client.post(
        "/api/v1/agent/chat",
        headers=headers,
        json={"agent_role": "HR", "message": message},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _departments(card: dict) -> set[str]:
    return {item["employee"]["department"] for item in card["items"]}


@pytest.mark.parametrize(
    "message, expected",
    [
        ("danh sách nhân viên", "EMPLOYEE_DIRECTORY"),
        ("danh sách nhân viên phòng IT", "EMPLOYEE_DIRECTORY"),
        # Naming a department asks about a group even without a "list" verb.
        ("thông tin nhân viên phòng IT", "EMPLOYEE_DIRECTORY"),
        ("cho tôi xem danh sách quản lý", "MANAGER_DIRECTORY"),
        ("danh sách giám đốc", "MANAGER_DIRECTORY"),
        ("danh sách ban giám đốc", "MANAGER_DIRECTORY"),
        ("thông tin nhân viên số 40", "EMPLOYEE_SEARCH"),
        ("thông tin nhân viên Trần Bình", "EMPLOYEE_SEARCH"),
        ("chi tiết nhân viên NV-007", "EMPLOYEE_SEARCH"),
        ("tra cứu nhân viên test12@gmail.com", "EMPLOYEE_SEARCH"),
        # Unchanged by the wider search markers.
        ("hồ sơ của tôi", "SELF_PROFILE"),
        ("thông tin của tôi", "SELF_PROFILE"),
        ("tôi còn bao nhiêu ngày phép", "QUERY_LEAVE_BALANCE"),
    ],
)
def test_intent_routing(message, expected):
    assert _classify_hr_intent(message) == expected


@pytest.mark.parametrize(
    "message, expected",
    [
        ("thông tin nhân viên số 40", "40"),
        ("chi tiết nhân viên mã NV-007", "NV-007"),
        ("xem thông tin nhân viên Trần Bình", "Trần Bình"),
        ("tìm nhân viên An", "An"),
        ("tra cứu nhân viên test12@gmail.com", "test12@gmail.com"),
        # Naming nobody must stay naming nobody rather than searching for a pronoun.
        ("thông tin của tôi", ""),
        ("tra cứu nhân viên", ""),
    ],
)
def test_employee_search_term(message, expected):
    assert _extract_employee_search_term(message) == expected


def _seeded_ceo(db) -> User:
    tenant = db.query(Tenant).filter(Tenant.domain == "acme.com").one()
    return db.query(User).filter(
        User.tenant_id == tenant.id, User.email == "admin@company.com"
    ).one()


@pytest.mark.parametrize(
    "message, expected_codes, expected_mention",
    [
        ("danh sách nhân viên phòng IT", ("IT",), True),
        ("liệt kê nhân viên phòng công nghệ thông tin", ("IT",), True),
        ("bộ phận marketing có bao nhiêu người", ("MARKETING",), True),
        ("danh sách nhân viên", (), False),
        # A department this company does not have is reported, never ignored.
        ("danh sách nhân viên phòng hậu cần", (), True),
    ],
)
def test_resolve_requested_departments(
    transactional_db_session, message, expected_codes, expected_mention
):
    actor = _seeded_ceo(transactional_db_session)
    codes, mentioned = _resolve_requested_departments(
        transactional_db_session, actor, message
    )
    assert codes == expected_codes
    assert mentioned is expected_mention


def test_department_name_does_not_swallow_an_ordinary_word(transactional_db_session):
    """A department called "Nhân sự" must not turn every HR question into a filter.

    Only the code and the full name are recognised anywhere in a sentence; the name
    stripped of its leading "Phòng" is recognised only right after that word.
    """
    actor = _seeded_ceo(transactional_db_session)
    codes, _ = _resolve_requested_departments(
        transactional_db_session, actor, "cho tôi xem danh sách quản lý"
    )
    assert codes == ()


def test_supervisory_roles_include_the_founder(transactional_db_session):
    tenant = transactional_db_session.query(Tenant).filter(
        Tenant.domain == "acme.com"
    ).one()
    roles = supervisory_role_names(transactional_db_session, tenant.id)
    assert "CEO" in roles
    assert "Manager" in roles
    assert "Employee" not in roles
    assert "Guest" not in roles


def test_department_request_lists_only_that_department(client, ceo_token_headers):
    data = _chat(client, ceo_token_headers, "danh sách nhân viên phòng IT")
    card = data["hr_card"]
    assert card["department_filter"] == ["IT"]
    assert _departments(card) == {"IT"}
    assert card["total_count"] == len(card["items"])
    assert data["tools_executed"][0]["input"]["departments"] == ["IT"]
    # The count in the sentence has to be the count of what was actually returned.
    assert str(card["total_count"]) in data["reply"]


def test_department_named_in_words_matches_its_code(client, ceo_token_headers):
    data = _chat(client, ceo_token_headers, "liệt kê nhân viên phòng công nghệ thông tin")
    assert data["hr_card"]["department_filter"] == ["IT"]
    assert _departments(data["hr_card"]) == {"IT"}


def test_unknown_department_is_reported_not_ignored(client, ceo_token_headers):
    data = _chat(client, ceo_token_headers, "danh sách nhân viên phòng hậu cần")
    assert data["hr_card"] is None
    assert "không tìm thấy phòng ban" in data["reply"].lower()


def test_department_filter_never_widens_scope(client, employee_token_headers):
    """An employee asking about their own department still sees only themselves."""
    data = _chat(client, employee_token_headers, "danh sách nhân viên phòng IT")
    card = data["hr_card"]
    assert card["scope"] == "SELF"
    assert [item["employee"]["email"] for item in card["items"]] == [
        "employee@company.com"
    ]


def test_manager_directory_includes_the_founder(client, ceo_token_headers):
    data = _chat(client, ceo_token_headers, "danh sách giám đốc")
    roles = {item["employee"]["role"] for item in data["hr_card"]["items"]}
    assert data["hr_card"]["directory_type"] == "MANAGERS"
    assert "CEO" in roles
    assert "Employee" not in roles


def test_employee_lookup_by_name(client, ceo_token_headers):
    data = _chat(client, ceo_token_headers, "thông tin nhân viên Phạm Văn Tech")
    assert data["hr_card"] is not None
    assert "Phạm Văn Tech" in data["reply"]
