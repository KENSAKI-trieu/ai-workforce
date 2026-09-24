"""Deterministic LLM token pricing utilities.

Prices are stored per one million tokens and calculations use ``Decimal`` so
small requests are not rounded to zero before they are aggregated.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


PRICING_VERSION = "2026-07-31"
TOKENS_PER_MILLION = Decimal("1000000")
COST_QUANTUM = Decimal("0.000000001")


class UnsupportedModelPricingError(ValueError):
    """Raised when usage cannot be priced without guessing."""


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal


MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-4o": ModelPricing(Decimal("2.50"), Decimal("10.00"), Decimal("1.25")),
    # The default chat model of the AI service. It needs its own row rather than the
    # `gpt-4o-` prefix alias below, which would have priced every mini call at the full
    # gpt-4o rate -- roughly sixteen times its actual cost. An exact key wins over a
    # prefix in `normalize_model_name`, so the alias now only catches dated snapshots.
    "gpt-4o-mini": ModelPricing(
        Decimal("0.15"), Decimal("0.60"), Decimal("0.075")
    ),
    "gpt-3.5-turbo": ModelPricing(
        Decimal("0.50"), Decimal("1.50"), Decimal("0.50")
    ),
    "gemini-2.5-flash": ModelPricing(
        Decimal("0.30"), Decimal("2.50"), Decimal("0.30")
    ),
    # The AI service's default Gemini chat model. Priced provisionally at the 2.5-flash
    # rate so its usage reaches the dashboard at all; this is an approximation carried
    # over from the previous generation, not a published figure, and has to be replaced
    # once the real price is known. Rows already written keep `pricing_version`, so a
    # correction can be told apart from what was billed under this estimate.
    "gemini-3.6-flash": ModelPricing(
        Decimal("0.30"), Decimal("2.50"), Decimal("0.30")
    ),
    "claude-sonnet-4": ModelPricing(
        Decimal("3.00"), Decimal("15.00"), Decimal("0.30")
    ),
    # Legacy entries remain priceable for imported historical provider usage.
    "gemini-1.5-pro": ModelPricing(
        Decimal("1.25"), Decimal("5.00"), Decimal("0.3125")
    ),
    "gemini-1.5-flash": ModelPricing(
        Decimal("0.075"), Decimal("0.30"), Decimal("0.01875")
    ),
    "claude-3-5-sonnet": ModelPricing(
        Decimal("3.00"), Decimal("15.00"), Decimal("0.30")
    ),
}


MODEL_PREFIX_ALIASES: tuple[tuple[str, str], ...] = (
    # Ordered: the mini snapshots must be claimed before the broader `gpt-4o-` prefix,
    # which would otherwise price them as the full model.
    ("gpt-4o-mini-", "gpt-4o-mini"),
    ("gpt-4o-", "gpt-4o"),
    ("gpt-3.5-turbo-", "gpt-3.5-turbo"),
    ("gemini-2.5-flash-", "gemini-2.5-flash"),
    ("gemini-3.6-flash-", "gemini-3.6-flash"),
    ("claude-sonnet-4-", "claude-sonnet-4"),
    ("claude-3-5-sonnet-", "claude-3-5-sonnet"),
)


def normalize_model_name(model_name: str) -> str:
    """Resolve snapshots to a priced base model without losing the stored ID."""
    normalized = model_name.strip().lower()
    if not normalized:
        raise UnsupportedModelPricingError("model_name must not be empty")
    if normalized in MODEL_PRICING:
        return normalized
    for prefix, base_name in MODEL_PREFIX_ALIASES:
        if normalized.startswith(prefix):
            return base_name
    raise UnsupportedModelPricingError(
        f"No pricing configured for model '{model_name}'"
    )


def validate_token_usage(
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
) -> None:
    for field_name, value in (
        ("prompt_tokens", prompt_tokens),
        ("completion_tokens", completion_tokens),
        ("cached_prompt_tokens", cached_prompt_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field_name} must be an integer")
        if value < 0:
            raise ValueError(f"{field_name} must be greater than or equal to 0")
    if cached_prompt_tokens > prompt_tokens:
        raise ValueError("cached_prompt_tokens cannot exceed prompt_tokens")


def calculate_llm_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
) -> Decimal:
    """Calculate USD cost using the configured standard processing price."""
    validate_token_usage(prompt_tokens, completion_tokens, cached_prompt_tokens)
    pricing = MODEL_PRICING[normalize_model_name(model_name)]
    uncached_prompt_tokens = prompt_tokens - cached_prompt_tokens
    total = (
        Decimal(uncached_prompt_tokens) * pricing.input_per_million
        + Decimal(cached_prompt_tokens) * pricing.cached_input_per_million
        + Decimal(completion_tokens) * pricing.output_per_million
    ) / TOKENS_PER_MILLION
    return total.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def supported_model_names(*, include_legacy: bool = False) -> set[str]:
    current = {
        "gpt-4o",
        "gpt-3.5-turbo",
        "gemini-2.5-flash",
        "claude-sonnet-4",
    }
    return set(MODEL_PRICING) if include_legacy else current
