"""Packages a workspace writes for itself: create, edit, delete, and their limits."""

from __future__ import annotations

from app.plugins.manifest import parse_manifest
from app.plugins.resolver import build_skill_restriction, effective_tools
from app.agents.hr.prompts import default_prompt

KNOWN_TOOLS = frozenset({
    "rag_search",
    "query_leave_balance",
    "export_hr_directory",
})
KNOWN_ROLES = frozenset({"HR"})

NAME = "my-house-style"

AUTHORED_YAML = """
name: my-house-style
version: 1.0.0
display_name: "Quy ước nội bộ"
target_role: HR
prompts:
  answer: |
    Luôn xưng hô là anh/chị.
"""


def _create(client, headers, yaml_text=AUTHORED_YAML):
    return client.post(
        "/api/v1/plugins/authored", headers=headers, json={"source_yaml": yaml_text}
    )


def _cleanup(client, headers):
    """Uninstall then delete, ignoring whichever step has nothing to do."""
    client.delete(f"/api/v1/plugins/{NAME}", headers=headers)
    client.delete(f"/api/v1/plugins/authored/{NAME}", headers=headers)


def test_a_workspace_can_create_read_edit_and_delete_its_own_package(
    transactional_db_session, client, ceo_token_headers
):
    try:
        res = _create(client, ceo_token_headers)
        assert res.status_code == 200, res.text
        assert res.json()["name"] == NAME

        read = client.get(
            f"/api/v1/plugins/authored/{NAME}", headers=ceo_token_headers
        ).json()
        assert "anh/chị" in read["source_yaml"]

        edited = AUTHORED_YAML.replace("1.0.0", "1.1.0").replace(
            "Luôn xưng hô là anh/chị.", "Luôn xưng hô là quý khách."
        )
        res = client.put(
            f"/api/v1/plugins/authored/{NAME}",
            headers=ceo_token_headers,
            json={"source_yaml": edited},
        )
        assert res.status_code == 200, res.text

        read = client.get(
            f"/api/v1/plugins/authored/{NAME}", headers=ceo_token_headers
        ).json()
        assert read["version"] == "1.1.0"
        assert "quý khách" in read["source_yaml"]

        assert (
            client.delete(
                f"/api/v1/plugins/authored/{NAME}", headers=ceo_token_headers
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/plugins/authored/{NAME}", headers=ceo_token_headers
            ).status_code
            == 404
        )
    finally:
        _cleanup(client, ceo_token_headers)


def test_an_authored_package_can_be_installed_and_reaches_the_model(
    transactional_db_session, client, ceo_token_headers
):
    """The whole point: a package typed in the product changes what the model is sent."""
    try:
        _create(client, ceo_token_headers)
        res = client.post(f"/api/v1/plugins/{NAME}/install", headers=ceo_token_headers)
        assert res.status_code == 200, res.text

        preview = client.get(
            "/api/v1/plugins/preview/HR/answer", headers=ceo_token_headers
        ).json()
        assert preview["is_overridden"] is True
        assert preview["resolved_prompt"].startswith(default_prompt("answer"))
        assert "anh/chị" in preview["resolved_prompt"]
        assert NAME in preview["contributing_plugins"]
    finally:
        _cleanup(client, ceo_token_headers)


def test_editing_an_installed_package_takes_effect_without_reinstalling(
    transactional_db_session, client, ceo_token_headers
):
    try:
        _create(client, ceo_token_headers)
        client.post(f"/api/v1/plugins/{NAME}/install", headers=ceo_token_headers)

        client.put(
            f"/api/v1/plugins/authored/{NAME}",
            headers=ceo_token_headers,
            json={
                "source_yaml": AUTHORED_YAML.replace(
                    "Luôn xưng hô là anh/chị.", "ĐÃ SỬA."
                )
            },
        )
        preview = client.get(
            "/api/v1/plugins/preview/HR/answer", headers=ceo_token_headers
        ).json()
        assert "ĐÃ SỬA." in preview["resolved_prompt"]
        assert "anh/chị" not in preview["resolved_prompt"]
    finally:
        _cleanup(client, ceo_token_headers)


def test_deleting_an_installed_package_is_refused(
    transactional_db_session, client, ceo_token_headers
):
    """Otherwise the agent changes how it answers with nothing on screen saying so."""
    try:
        _create(client, ceo_token_headers)
        client.post(f"/api/v1/plugins/{NAME}/install", headers=ceo_token_headers)

        res = client.delete(
            f"/api/v1/plugins/authored/{NAME}", headers=ceo_token_headers
        )
        assert res.status_code == 409
        assert "uninstall" in res.json()["detail"].lower()
    finally:
        _cleanup(client, ceo_token_headers)


def test_an_authored_package_cannot_take_a_built_in_name(
    transactional_db_session, client, ceo_token_headers
):
    res = _create(
        client, ceo_token_headers, AUTHORED_YAML.replace(NAME, "company-a-hr")
    )
    assert res.status_code == 422
    assert "built-in" in res.json()["detail"]


def test_a_name_cannot_be_changed_by_an_edit(
    transactional_db_session, client, ceo_token_headers
):
    """A rename would orphan the install row and silently drop back to defaults."""
    try:
        _create(client, ceo_token_headers)
        res = client.put(
            f"/api/v1/plugins/authored/{NAME}",
            headers=ceo_token_headers,
            json={"source_yaml": AUTHORED_YAML.replace(NAME, "renamed-package")},
        )
        assert res.status_code == 422
    finally:
        _cleanup(client, ceo_token_headers)


def test_an_invalid_manifest_is_refused_with_a_usable_reason(
    transactional_db_session, client, ceo_token_headers
):
    res = _create(
        client, ceo_token_headers, AUTHORED_YAML.replace("answer:", "not_a_slot:")
    )
    assert res.status_code == 422
    assert "not_a_slot" in res.json()["detail"]


def test_authoring_in_the_product_is_not_a_way_around_the_narrowing_rule(
    transactional_db_session, client, ceo_token_headers
):
    """A typed package is held to the same rule as a shipped one: it may only narrow."""
    yaml_text = """
name: my-house-style
version: 1.0.0
display_name: "Thử mở rộng quyền"
target_role: HR
skills:
  tools_access:
    - rag_search
    - export_hr_directory
"""
    try:
        assert _create(client, ceo_token_headers, yaml_text).status_code == 200

        # The manifest stores fine; what it cannot do is grant. Proven at the resolver,
        # which every tool check goes through.
        parsed = parse_manifest(
            {
                "name": "probe-package",
                "version": "1.0.0",
                "target_role": "HR",
                "skills": {"tools_access": ["rag_search", "export_hr_directory"]},
            },
            known_tools=KNOWN_TOOLS,
            known_roles=KNOWN_ROLES,
        )
        restriction = build_skill_restriction([parsed])
        assert effective_tools({"rag_search"}, restriction) == frozenset(
            {"rag_search"}
        )
    finally:
        _cleanup(client, ceo_token_headers)


def test_an_employee_cannot_author_or_list_packages(
    transactional_db_session, client, employee_token_headers
):
    assert _create(client, employee_token_headers).status_code == 403
    assert (
        client.get(
            "/api/v1/plugins/authored", headers=employee_token_headers
        ).status_code
        == 403
    )


def test_validate_reports_the_problem_without_saving_anything(
    transactional_db_session, client, ceo_token_headers
):
    bad = client.post(
        "/api/v1/plugins/validate",
        headers=ceo_token_headers,
        json={"source_yaml": "name: broken\nversion: nope\ntarget_role: HR"},
    ).json()
    assert bad["valid"] is False
    assert "version" in bad["error"]

    good = client.post(
        "/api/v1/plugins/validate",
        headers=ceo_token_headers,
        json={"source_yaml": AUTHORED_YAML},
    ).json()
    assert good["valid"] is True
    assert good["manifest"]["target_role"] == "HR"

    # Validating must not be a side door that creates the package.
    assert (
        client.get(
            f"/api/v1/plugins/authored/{NAME}", headers=ceo_token_headers
        ).status_code
        == 404
    )


def test_the_catalogue_marks_which_packages_are_editable(
    transactional_db_session, client, ceo_token_headers
):
    try:
        _create(client, ceo_token_headers)
        catalogue = {
            item["name"]: item
            for item in client.get("/api/v1/plugins/", headers=ceo_token_headers).json()
        }
        assert catalogue["company-a-hr"]["editable"] is False
        assert catalogue[NAME]["editable"] is True
    finally:
        _cleanup(client, ceo_token_headers)
