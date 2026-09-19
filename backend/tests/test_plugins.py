"""Plugin manifests, prompt layering, and the rule that packages may only narrow access."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from app.models.models import AIAgent, TenantPluginInstall, User
from app.plugins.loader import load_manifest_file
from app.plugins.manifest import (
    PLATFORM_VERSION,
    PluginManifestError,
    parse_manifest,
)
from app.plugins.resolver import (
    SkillRestriction,
    build_prompt_overlay,
    build_skill_restriction,
    effective_tools,
)
from app.services.agents import agent_executor
from app.services.agents.hr_prompts import (
    DEFAULT_HR_PROMPTS,
    HR_PROMPT_SLOTS,
    default_prompt,
    resolve_slot,
)

KNOWN_TOOLS = frozenset({
    "hybrid_rag_search",
    "query_leave_balance",
    "export_hr_directory",
    "request_leave",
})
KNOWN_ROLES = frozenset({"HR", "LEGAL", "CEO"})


def make_manifest(**overrides):
    data = {
        "name": "test-plugin",
        "version": "1.0.0",
        "display_name": "Test",
        "target_role": "HR",
        "prompts": {"answer": "Extra house style."},
    }
    data.update(overrides)
    return parse_manifest(data, known_tools=KNOWN_TOOLS, known_roles=KNOWN_ROLES)


# --------------------------------------------------------------------------
# Manifest validation
# --------------------------------------------------------------------------

def test_a_valid_manifest_parses_with_append_as_the_default_mode():
    manifest = make_manifest()
    assert manifest.name == "test-plugin"
    assert manifest.prompts[0].slot == "answer"
    assert manifest.prompts[0].mode == "append"


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Has Capitals"},
        {"version": "1.0"},
        {"target_role": "MARKETING"},
        {"prompts": {"not_a_slot": "text"}},
        {"prompts": {"answer": {"mode": "overwrite", "text": "x"}}},
        {"prompts": {"answer": {"mode": "append", "text": "   "}}},
        {"skills": {"tools_access": ["no_such_tool"]}},
        {"skills": {"unknown_key": []}},
        {"min_platform_version": PLATFORM_VERSION + 1},
    ],
)
def test_malformed_manifests_are_rejected(overrides):
    with pytest.raises(PluginManifestError):
        make_manifest(**overrides)


def test_a_manifest_that_changes_nothing_is_rejected():
    """A package with no effect installs cleanly and does nothing, which is a trap."""
    with pytest.raises(PluginManifestError):
        parse_manifest(
            {"name": "empty", "version": "1.0.0", "target_role": "HR"},
            known_tools=KNOWN_TOOLS,
            known_roles=KNOWN_ROLES,
        )


def test_a_tool_cannot_be_granted_and_denied_by_the_same_package():
    with pytest.raises(PluginManifestError):
        make_manifest(
            skills={
                "tools_access": ["hybrid_rag_search"],
                "disallowed_actions": ["hybrid_rag_search"],
            }
        )


def test_unknown_top_level_keys_are_rejected_rather_than_ignored():
    with pytest.raises(PluginManifestError):
        make_manifest(prompt="typo for prompts")


# --------------------------------------------------------------------------
# Prompt layering
# --------------------------------------------------------------------------

def test_no_installed_plugin_yields_the_shipped_prompt_byte_for_byte():
    for slot in HR_PROMPT_SLOTS:
        assert resolve_slot(None, slot) == DEFAULT_HR_PROMPTS[slot]
        assert resolve_slot({}, slot) == DEFAULT_HR_PROMPTS[slot]
        assert resolve_slot({slot: "   "}, slot) == DEFAULT_HR_PROMPTS[slot]


def test_append_keeps_the_default_and_adds_the_package_text():
    manifest = make_manifest(prompts={"answer": "Always address the user formally."})
    overlay = build_prompt_overlay([manifest])
    assert overlay["answer"].startswith(default_prompt("answer"))
    assert overlay["answer"].endswith("Always address the user formally.")


def test_replace_drops_the_default_entirely():
    manifest = make_manifest(
        prompts={"answer": {"mode": "replace", "text": "Only this."}}
    )
    overlay = build_prompt_overlay([manifest])
    assert overlay["answer"] == "Only this."


def test_two_packages_touching_one_slot_layer_in_install_order():
    first = make_manifest(name="first", prompts={"answer": "FIRST"})
    second = make_manifest(name="second", prompts={"answer": "SECOND"})
    overlay = build_prompt_overlay([first, second])
    assert overlay["answer"].index("FIRST") < overlay["answer"].index("SECOND")


def test_only_the_declared_slots_are_overridden():
    manifest = make_manifest(prompts={"answer": "House style."})
    overlay = build_prompt_overlay([manifest])
    assert "classifier" not in overlay
    assert resolve_slot(overlay, "classifier") == default_prompt("classifier")


# --------------------------------------------------------------------------
# The narrowing rule: a package may remove access and may never add it
# --------------------------------------------------------------------------

def test_a_package_cannot_grant_a_tool_the_agent_does_not_have():
    """The whole security property of the plugin system, stated directly."""
    agent_tools = {"hybrid_rag_search"}
    manifest = make_manifest(
        skills={"tools_access": ["hybrid_rag_search", "export_hr_directory"]}
    )
    restriction = build_skill_restriction([manifest])
    assert effective_tools(agent_tools, restriction) == frozenset({"hybrid_rag_search"})
    assert "export_hr_directory" not in effective_tools(agent_tools, restriction)


def test_a_package_can_withdraw_a_tool_the_agent_has():
    agent_tools = {"hybrid_rag_search", "export_hr_directory"}
    manifest = make_manifest(skills={"disallowed_actions": ["export_hr_directory"]})
    restriction = build_skill_restriction([manifest])
    assert effective_tools(agent_tools, restriction) == frozenset({"hybrid_rag_search"})


def test_two_restricting_packages_intersect_rather_than_union():
    agent_tools = {"hybrid_rag_search", "query_leave_balance", "request_leave"}
    first = make_manifest(
        name="first", skills={"tools_access": ["hybrid_rag_search", "query_leave_balance"]}
    )
    second = make_manifest(
        name="second", skills={"tools_access": ["query_leave_balance", "request_leave"]}
    )
    restriction = build_skill_restriction([first, second])
    assert effective_tools(agent_tools, restriction) == frozenset({"query_leave_balance"})


def test_a_denial_beats_another_packages_grant():
    agent_tools = {"hybrid_rag_search", "export_hr_directory"}
    granting = make_manifest(
        name="granting",
        skills={"tools_access": ["hybrid_rag_search", "export_hr_directory"]},
    )
    denying = make_manifest(
        name="denying", skills={"disallowed_actions": ["export_hr_directory"]}
    )
    restriction = build_skill_restriction([granting, denying])
    assert effective_tools(agent_tools, restriction) == frozenset({"hybrid_rag_search"})


def test_no_restriction_is_not_the_same_as_an_empty_one():
    """An absent tools_access must leave everything alone, not withdraw everything."""
    agent_tools = {"hybrid_rag_search", "query_leave_balance"}
    unrestricted = build_skill_restriction([make_manifest()])
    assert unrestricted.allowed is None
    assert effective_tools(agent_tools, unrestricted) == frozenset(agent_tools)

    withdraw_all = SkillRestriction(allowed=frozenset())
    assert effective_tools(agent_tools, withdraw_all) == frozenset()


# --------------------------------------------------------------------------
# Enforcement inside the agent executor
# --------------------------------------------------------------------------

def _hr_agent_double() -> AIAgent:
    agent = AIAgent(
        tenant_id=uuid.uuid4(),
        name="HR Agent",
        role_code="HR",
        system_prompt="x",
        tools_access=["hybrid_rag_search", "export_hr_directory"],
        allowed_actions=["hybrid_rag_search", "export_hr_directory"],
        disallowed_actions=[],
        configuration_version=99,
    )
    return agent


def test_require_tool_rejects_what_an_installed_package_withdrew():
    agent = _hr_agent_double()
    assert agent_executor._can_use_tool(agent, "export_hr_directory") is True

    agent_executor._attach_plugin_restriction(
        agent, SkillRestriction(denied=frozenset({"export_hr_directory"}))
    )

    assert agent_executor._can_use_tool(agent, "export_hr_directory") is False
    with pytest.raises(Exception) as excinfo:
        agent_executor._require_tool(agent, "export_hr_directory")
    assert "plugin" in str(excinfo.value).lower()


def test_a_restriction_never_turns_a_denied_tool_back_on():
    agent = _hr_agent_double()
    agent.disallowed_actions = ["export_hr_directory"]
    agent_executor._attach_plugin_restriction(
        agent, SkillRestriction(allowed=frozenset({"export_hr_directory"}))
    )
    assert agent_executor._can_use_tool(agent, "export_hr_directory") is False


def test_narrowing_is_not_written_back_to_the_agent_row():
    """Uninstalling must restore access, so the narrowing stays out of the columns."""
    agent = _hr_agent_double()
    original_tools = list(agent.tools_access)
    agent_executor._attach_plugin_restriction(
        agent, SkillRestriction(allowed=frozenset({"hybrid_rag_search"}))
    )
    agent_executor._can_use_tool(agent, "export_hr_directory")
    assert agent.tools_access == original_tools
    assert agent.disallowed_actions == []


# --------------------------------------------------------------------------
# Packages shipped in the repository
# --------------------------------------------------------------------------

def test_every_shipped_package_validates():
    root = Path(__file__).resolve().parents[1] / "plugins"
    manifests = [load_manifest_file(path) for path in sorted(root.glob("*/plugin.yaml"))]
    assert manifests, "no plugin packages were found to validate"
    for manifest in manifests:
        assert manifest.target_role in {"HR", "LEGAL", "CEO", "IT", "FINANCE", "SALES", "KNOWLEDGE"}


def test_a_packages_directory_must_match_its_declared_name(tmp_path):
    package = tmp_path / "wrong-directory"
    package.mkdir()
    (package / "plugin.yaml").write_text(
        yaml.safe_dump({
            "name": "declared-name",
            "version": "1.0.0",
            "target_role": "HR",
            "prompts": {"answer": "text"},
        }),
        encoding="utf-8",
    )
    with pytest.raises(PluginManifestError):
        load_manifest_file(package / "plugin.yaml")


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------

def test_catalogue_lists_the_shipped_packages(client, ceo_token_headers):
    res = client.get("/api/v1/plugins/", headers=ceo_token_headers)
    assert res.status_code == 200
    names = {item["name"] for item in res.json()}
    assert "company-a-hr" in names


def test_install_and_uninstall_round_trip(client, ceo_token_headers):
    res = client.post("/api/v1/plugins/company-a-hr/install", headers=ceo_token_headers)
    assert res.status_code == 200, res.text
    assert res.json()["target_role"] == "HR"

    installed = client.get("/api/v1/plugins/installed", headers=ceo_token_headers)
    assert "company-a-hr" in {row["plugin_name"] for row in installed.json()}

    res = client.delete("/api/v1/plugins/company-a-hr", headers=ceo_token_headers)
    assert res.status_code == 200

    installed = client.get("/api/v1/plugins/installed", headers=ceo_token_headers)
    assert "company-a-hr" not in {row["plugin_name"] for row in installed.json()}


def test_installing_an_unknown_package_is_a_404(client, ceo_token_headers):
    res = client.post("/api/v1/plugins/no-such-plugin/install", headers=ceo_token_headers)
    assert res.status_code == 404


def test_an_employee_cannot_install_a_package(client, employee_token_headers):
    res = client.post("/api/v1/plugins/company-a-hr/install", headers=employee_token_headers)
    assert res.status_code == 403


def test_preview_shows_the_default_until_a_package_is_installed(client, ceo_token_headers):
    res = client.get("/api/v1/plugins/preview/HR/answer", headers=ceo_token_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["is_overridden"] is False
    assert body["resolved_prompt"] == default_prompt("answer")

    client.post("/api/v1/plugins/company-a-hr/install", headers=ceo_token_headers)
    try:
        res = client.get("/api/v1/plugins/preview/HR/answer", headers=ceo_token_headers)
        body = res.json()
        assert body["is_overridden"] is True
        assert body["resolved_prompt"].startswith(default_prompt("answer"))
        assert "company-a-hr" in body["contributing_plugins"]
    finally:
        client.delete("/api/v1/plugins/company-a-hr", headers=ceo_token_headers)


def test_preview_rejects_an_unknown_slot(client, ceo_token_headers):
    res = client.get("/api/v1/plugins/preview/HR/not_a_slot", headers=ceo_token_headers)
    assert res.status_code == 404
