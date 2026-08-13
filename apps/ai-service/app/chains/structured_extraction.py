from collections.abc import Callable, Iterable
from typing import TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from app.models.factory import configured_chat_models
from app.schemas.findings import ContractFindings
from app.schemas.routing import AgentRoutingDecision


SchemaT = TypeVar("SchemaT", bound=BaseModel)


def invoke_structured(
    model: BaseChatModel,
    messages: list[dict[str, str]],
    schema: type[SchemaT],
) -> SchemaT:
    """Use provider-native JSON schema output and validate it with Pydantic."""
    structured_model = model.with_structured_output(schema, method="json_schema")
    response = structured_model.invoke(messages)
    return response if isinstance(response, schema) else schema.model_validate(response)


def invoke_structured_with_fallback(
    models: Iterable[BaseChatModel],
    messages: list[dict[str, str]],
    schema: type[SchemaT],
    *,
    fallback: Callable[[], SchemaT] | None = None,
    validator: Callable[[SchemaT], SchemaT] | None = None,
) -> SchemaT:
    failures: list[str] = []
    for model in models:
        try:
            result = invoke_structured(model, messages, schema)
            return validator(result) if validator is not None else result
        except Exception as exc:
            failures.append(exc.__class__.__name__)
    if fallback is not None:
        result = schema.model_validate(fallback())
        return validator(result) if validator is not None else result
    raise RuntimeError(f"All structured-output models failed: {', '.join(failures)}")


def extract_agent_routing(
    message: str,
    available_roles: dict[str, str],
    *,
    models: Iterable[BaseChatModel] | None = None,
    fallback_role: str,
) -> AgentRoutingDecision:
    normalized_roles = {role.upper(): description for role, description in available_roles.items()}

    def validate_role(decision: AgentRoutingDecision) -> AgentRoutingDecision:
        if decision.role not in normalized_roles:
            raise ValueError(f"Unregistered agent role: {decision.role}")
        return decision

    selected_models = configured_chat_models() if models is None else models
    return invoke_structured_with_fallback(
        selected_models,
        [
            {
                "role": "system",
                "content": (
                    "Route the message to exactly one registered agent role. "
                    f"Available roles: {normalized_roles}"
                ),
            },
            {"role": "user", "content": message},
        ],
        AgentRoutingDecision,
        fallback=lambda: AgentRoutingDecision(
            role=fallback_role,
            confidence=0.0,
            reason="Deterministic registry fallback",
        ),
        validator=validate_role,
    )


def extract_contract_findings(
    contract_text: str,
    *,
    models: Iterable[BaseChatModel] | None = None,
    fallback: Callable[[], ContractFindings] | None = None,
) -> ContractFindings:
    def validate_evidence(result: ContractFindings) -> ContractFindings:
        if any(finding.evidence not in contract_text for finding in result.findings):
            raise ValueError("Finding evidence does not exist in the contract text")
        return result

    selected_models = configured_chat_models() if models is None else models
    return invoke_structured_with_fallback(
        selected_models,
        [
            {
                "role": "system",
                "content": (
                    "Extract contract risks only when supported by the contract text. "
                    "Do not invent evidence or legal citations."
                ),
            },
            {"role": "user", "content": contract_text},
        ],
        ContractFindings,
        fallback=fallback,
        validator=validate_evidence,
    )
