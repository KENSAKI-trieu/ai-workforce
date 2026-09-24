"""Model-backed and deterministic decision providers for graph nodes."""

from __future__ import annotations

import json
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field, model_validator

from app.governance.middleware.context import AgentRuntimeContext
from app.governance.middleware.observability import TelemetrySink
from app.governance.middleware.stack import create_governed_agent
from app.orchestration.state import WorkforceAgentState


class GraphDecision(BaseModel):
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    final_answer: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_tool_or_answer(self) -> "GraphDecision":
        if not self.tool_name and not self.final_answer:
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
            final_answer="No governed source or tool result is available for this request.",
            reason="Fail closed when no evidence is available.",
        )


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
    ) -> None:
        contract_text = json.dumps(tool_contracts, ensure_ascii=False, default=str)
        system_prompt = (
            "You are the model/tool decision node in a governed enterprise graph. "
            "Choose at most one available tool or return a final answer. Never add tenant, "
            "identity, role, ACL, or audit arguments; orchestration injects them. "
            f"Available tool contracts: {contract_text}"
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
            "intent": state.get("intent"),
            "selected_agent": state.get("selected_agent"),
            "domain_prompt": state.get("domain_prompt"),
            "available_tools": state.get("available_tools", []),
            "retrieved_context": state.get("retrieved_context", []),
            "previous_tool_calls": state.get("tool_calls", []),
            "user_messages": state.get("messages", []),
        }
        result = self.agent.invoke(
            {"messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False, default=str)}]},
            context=self.runtime_context,
        )
        decision = result.get("structured_response")
        if not isinstance(decision, GraphDecision):
            raise ValueError("Decision model did not return GraphDecision")
        return decision
