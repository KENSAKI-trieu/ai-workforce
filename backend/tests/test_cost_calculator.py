"""Unit tests for precise, deterministic provider token pricing."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domains.platform.audit_service import get_month_bounds
from app.domains.platform.cost_calculator import (
    UnsupportedModelPricingError,
    calculate_llm_cost,
    normalize_model_name,
)


def test_standard_and_snapshot_pricing():
    expected = Decimal("0.012500000")
    assert calculate_llm_cost("gpt-4o", 1000, 1000) == expected
    assert calculate_llm_cost("gpt-4o-2024-11-20", 1000, 1000) == expected
    assert normalize_model_name("gpt-4o-2024-11-20") == "gpt-4o"


def test_cached_input_pricing():
    assert calculate_llm_cost(
        "gpt-4o", 1000, 1000, cached_prompt_tokens=1000
    ) == Decimal("0.011250000")


def test_tiny_cost_is_not_rounded_to_zero():
    assert calculate_llm_cost(
        "gemini-1.5-flash", 1, 0
    ) == Decimal("0.000000075")


@pytest.mark.parametrize(
    ("prompt", "completion", "cached"),
    [(-1, 0, 0), (0, -1, 0), (1, 0, -1), (1, 0, 2)],
)
def test_invalid_token_usage_is_rejected(prompt, completion, cached):
    with pytest.raises(ValueError):
        calculate_llm_cost(
            "gpt-4o", prompt, completion, cached_prompt_tokens=cached
        )


def test_unknown_model_does_not_use_a_fallback_price():
    with pytest.raises(UnsupportedModelPricingError):
        calculate_llm_cost("unpriced-model", 1000, 1000)


def test_month_bounds_are_utc_and_exclusive():
    start, end = get_month_bounds(
        now=datetime(2026, 12, 15, 10, tzinfo=timezone.utc)
    )
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_invalid_month_is_rejected():
    with pytest.raises(ValueError):
        get_month_bounds("2026-13")
