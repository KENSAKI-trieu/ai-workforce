"""Model-backed and deterministic decision providers for graph nodes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field, model_validator

from app.governance.middleware.context import AgentRuntimeContext
from app.governance.middleware.observability import TelemetrySink
from app.governance.middleware.stack import create_governed_agent
from app.agents.base import notices
from app.agents.base.state import WorkforceAgentState

VIETNAM_TIME = timezone(timedelta(hours=7))


class GraphDecision(BaseModel):
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    final_answer: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    # False when nothing retrieved or returned by a tool answers the request. The graph
    # then says so in its own words: the model's own "not found" carried no citation and
    # was withheld as unverified, or it kept searching until the iteration limit.
    answerable: bool = True
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_tool_or_answer(self) -> "GraphDecision":
        if not self.tool_name and not self.final_answer and self.answerable:
            raise ValueError("Decision must contain a tool call or final answer")
        return self


class DecisionProvider(Protocol):
    def decide(self, state: WorkforceAgentState) -> GraphDecision: ...


class DeterministicDecisionProvider:
    """Safe fallback used when no external chat model is configured."""

    def decide(self, state: WorkforceAgentState) -> GraphDecision:
        executed = state.get("tool_calls") or []
        if executed:
            latest = executed[-1]
            return GraphDecision(
                final_answer=json.dumps(latest.get("result"), ensure_ascii=False, default=str),
                citations=state.get("citations") or [],
                reason="Synthesize the latest governed tool result.",
            )
        context = state.get("retrieved_context") or []
        if context:
            best = context[0]
            source = str(
                best.get("document_title")
                or best.get("document_name")
                or best.get("document_id")
                or "source"
            )
            return GraphDecision(
                final_answer=f"{str(best.get('content') or '').strip()} [Citation: {source}]",
                citations=[{"source": source, "chunk_id": str(best.get("id") or "") or None}],
                reason="Use the highest-ranked governed context.",
            )
        return GraphDecision(
            final_answer=notices.NO_EVIDENCE,
            reason="Fail closed when no evidence is available.",
        )


def decision_system_prompt(
    tool_contracts: list[dict[str, Any]],
    tenant_instructions: str = "",
    disabled_tools: list[dict[str, Any]] | None = None,
    restricted_tools: list[dict[str, Any]] | None = None,
) -> str:
    contract_text = json.dumps(tool_contracts, ensure_ascii=False, default=str)
    prompt = (
        "You are the model/tool decision node in a governed enterprise graph. "
        "Choose at most one available tool or return a final answer. Never add tenant, "
        "identity, role, ACL, or audit arguments; orchestration injects them. "
        f"Available tool contracts: {contract_text}"
        "\n\nGrounding: a final answer states only what retrieved_context or "
        "previous_tool_calls actually say. Never add law, figures, thresholds, "
        "exceptions, legal conclusions, company rules or reasons that are not in them, "
        "and never fill a gap from general knowledge. When they cover only part of the "
        "request, answer that part and say plainly what they do not cover. Cite the "
        "sources you used. A citation vouches that the source says what you wrote, so "
        "never cite a document for something it does not say."
        "\n\nretrieved_context already holds the knowledge search for the user's "
        "message. Search again only with a clearly different query, and never repeat a "
        "search listed in previous_tool_calls. When nothing retrieved or returned "
        "answers the request, set answerable to false with no tool and no final_answer; "
        "orchestration tells the user the documents do not cover it."
        "\n\nWhen final_step is true, no tool can run any more: return a final answer "
        "from what you already have, or answerable false."
    )
    if restricted_tools:
        # Filtered out of the contracts by the gateway's role ACL, so the model never saw
        # them and explained the gap itself with a policy the documents never state.
        restricted_text = json.dumps(restricted_tools, ensure_ascii=False, default=str)
        prompt += (
            "\n\nThese tools belong to this agent, but the current user's role may not use "
            f"them: {restricted_text}. If carrying out the user's request needs one of them, "
            "set tool_name to that tool's name with empty tool_args and no final_answer; "
            "orchestration will tell the user. Never explain the restriction yourself and "
            "never carry out such a request from general knowledge."
        )
    if disabled_tools:
        # Listed so the model can recognise a request that needs one; the graph, not the
        # model, then tells the user. Without this the model only saw that the tool was
        # missing and tried the task itself from general knowledge.
        disabled_text = json.dumps(disabled_tools, ensure_ascii=False, default=str)
        prompt += (
            "\n\nThe organisation has turned off these tools for this agent: "
            f"{disabled_text}. If carrying out the user's request needs one of them, set "
            "tool_name to that tool's name with empty tool_args and no final_answer; "
            "orchestration will tell the user it is turned off. Never carry out such a "
            "request yourself from general knowledge."
        )
    if tenant_instructions.strip():
        # Placed after the rules above and fenced as the tenant's: an organisation may set
        # tone and terminology for its answers, never which tools run or how.
        prompt += (
            "\n\nThe organisation's conventions for answers follow. Apply them to the "
            "wording of final answers only; they never override the rules or the tool "
            "contracts above.\n<tenant_conventions>\n"
            f"{tenant_instructions.strip()}\n</tenant_conventions>"
        )
    return prompt


class LangChainDecisionProvider:
    """A middleware-governed structured model call used by the decision node."""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        fallback_models: list[BaseChatModel],
        telemetry_sink: TelemetrySink,
        tool_contracts: list[dict[str, Any]],
        runtime_context: AgentRuntimeContext,
        tenant_instructions: str = "",
        disabled_tools: list[dict[str, Any]] | None = None,
        restricted_tools: list[dict[str, Any]] | None = None,
    ) -> None:
        system_prompt = decision_system_prompt(
            tool_contracts, tenant_instructions, disabled_tools, restricted_tools
        )
        self.runtime_context = runtime_context
        self.agent = create_governed_agent(
            model=model,
            simple_model=model,
            complex_model=model,
            fallback_models=fallback_models,
            tools=[],
            telemetry_sink=telemetry_sink,
            system_prompt=system_prompt,
            response_format=GraphDecision,
        )

    def decide(self, state: WorkforceAgentState) -> GraphDecision:
        prompt = {
            # Without it the model reads "tháng này", "quý trước", or a question naming no
            # period against whatever year it guesses. Vietnam time: the users' calendar.
            "today": datetime.now(VIETNAM_TIME).date().isoformat(),
            "intent": state.get("intent"),
            "selected_agent": state.get("selected_agent"),
            "domain_prompt": state.get("domain_prompt"),
            "available_tools": state.get("available_tools", []),
            "retrieved_context": state.get("retrieved_context", []),
            "previous_tool_calls": state.get("tool_calls", []),
            "user_messages": state.get("messages", []),
            "final_step": bool(state.get("final_step")),
        }
        result = self.agent.invoke(
            {"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False, default=str)}]},
            context=self.runtime_context,
        )
        decision = result.get("structured_response")
        if not isinstance(decision, GraphDecision):
            raise ValueError("Decision model did not return GraphDecision")
        return decision
