"""The HR agent's graph: its capabilities as tools, and writes the user's request authorises."""

from __future__ import annotations

from app.agents.base.nodes import after_decision
from app.agents.hr.agent import POLICY
from app.agents.hr.tools import TOOLS
from app.tools.registry import tool_from_contract


def test_the_hr_ceiling_holds_its_capabilities_and_not_the_generic_tools() -> None:
    assert POLICY.tools == TOOLS
    assert {"request_leave", "query_leave_balance", "query_company_users_sql"} <= set(TOOLS)
    # The generic profile tools take a model-chosen purpose that widens what is released;
    # HR's own tools read who is meant from the user's message instead.
    assert not {"employee_lookup", "leave_lookup", "create_task", "submit_approval_request"} & set(TOOLS)


def _pending(**flags) -> dict:
    return {"pending_tool_call": {"name": "t", "action": "WRITE", **flags}}


def test_a_write_the_user_asked_for_runs_without_an_approval_stop() -> None:
    assert after_decision(_pending()) == "approval_interrupt"
    assert after_decision(_pending(runs_on_request=True)) == "execute_read_tool"
    assert after_decision(_pending(opens_approval=True)) == "execute_read_tool"


class _Gateway:
    def invoke(self, *_args, **_kwargs):
        return {}

    async def ainvoke(self, *_args, **_kwargs):
        return {}


def test_the_flag_travels_from_the_backend_contract() -> None:
    tool = tool_from_contract(
        {
            "name": "cancel_leave_request",
            "description": "Withdraw a leave request.",
            "action": "WRITE",
            "terminal": True,
            "runs_on_request": True,
            "input_schema": {"type": "object", "properties": {}},
        },
        _Gateway(),  # type: ignore[arg-type]
    )
    assert tool.metadata["runs_on_request"] is True
    assert tool.metadata["opens_approval"] is False
