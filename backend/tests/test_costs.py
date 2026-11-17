from decimal import Decimal

import pytest

from app.config import DEFAULT_ANTHROPIC_MODEL, Settings
from app.llm.client import TokenUsage
from app.services.costs import account_usage

PRICES = {
    "input_usd_per_million": "5",
    "cached_input_usd_per_million": "0.5",
    "output_usd_per_million": "25",
    "cache_write_5m_usd_per_million": "6.25",
    "cache_write_1h_usd_per_million": "10",
}


def claude_usage(**changes):
    return TokenUsage(
        **{
            "input_tokens": 100,
            "output_tokens": 10,
            "cached_input_tokens": 20,
            "cache_write_input_tokens": 30,
            "cache_write_5m_input_tokens": 10,
            "cache_write_1h_input_tokens": 20,
            **changes,
        }
    )


def test_openai_cache_hits_are_subtracted_and_reasoning_is_not_billed_twice():
    usage = TokenUsage(
        input_tokens=100,
        cached_input_tokens=20,
        output_tokens=10,
        reasoning_output_tokens=3,
        total_tokens=110,
    )
    result = account_usage("openai", usage, PRICES)
    assert result.status == "calculated"
    assert result.input_tokens == 100
    assert result.output_tokens == 10
    assert result.cost_usd == Decimal("0.0006600000")
    assert {name: Decimal(value) for name, value in result.components.items()} == {
        "input": Decimal("0.0004"),
        "cached_input": Decimal("0.00001"),
        "output": Decimal("0.00025"),
    }


@pytest.mark.parametrize("provider", ["anthropic", "foundry"])
def test_claude_counts_and_prices_cache_categories_separately(provider):
    result = account_usage(provider, claude_usage(), PRICES)
    assert result.status == "calculated"
    assert result.input_tokens == 150
    assert result.output_tokens == 10
    assert result.cost_usd == Decimal("0.0010225000")
    assert Decimal(result.components["cache_write_5m"]) == Decimal("0.0000625")
    assert Decimal(result.components["cache_write_1h"]) == Decimal("0.0002")


def test_reported_ttl_breakdown_can_supply_write_total_without_mutating_raw_usage():
    usage = claude_usage(cache_write_input_tokens=None)
    result = account_usage("foundry", usage, PRICES)
    assert result.input_tokens == 150
    assert result.cost_usd == Decimal("0.0010225000")
    assert usage.cache_write_input_tokens is None


def test_zero_cache_writes_need_neither_ttl_breakdown_nor_write_rates():
    usage = claude_usage(
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        cache_write_5m_input_tokens=None,
        cache_write_1h_input_tokens=None,
    )
    result = account_usage("foundry", usage, {k: v for k, v in PRICES.items() if "write" not in k})
    assert result.input_tokens == 100
    assert result.cost_usd == Decimal("0.00075")


@pytest.mark.parametrize(
    "provider,usage,prices,status",
    [
        ("openai", None, PRICES, "usage_unavailable"),
        ("openai", TokenUsage(input_tokens=100), PRICES, "usage_unavailable"),
        (
            "openai",
            TokenUsage(input_tokens=100, output_tokens=10),
            PRICES,
            "cache_usage_unavailable",
        ),
        ("foundry", claude_usage(cached_input_tokens=None), PRICES, "cache_usage_unavailable"),
        (
            "foundry",
            claude_usage(cache_write_1h_input_tokens=None),
            PRICES,
            "cache_usage_unavailable",
        ),
        (
            "foundry",
            claude_usage(cache_write_input_tokens=None, cache_write_5m_input_tokens=None),
            PRICES,
            "cache_usage_unavailable",
        ),
        ("foundry", claude_usage(), None, "pricing_unavailable"),
        (
            "foundry",
            claude_usage(),
            {k: v for k, v in PRICES.items() if "write" not in k},
            "cache_pricing_unavailable",
        ),
        ("fake", TokenUsage(input_tokens=100, output_tokens=10), PRICES, "simulated"),
    ],
)
def test_missing_information_never_becomes_a_zero_cost(provider, usage, prices, status):
    result = account_usage(provider, usage, prices)
    assert result.status == status
    assert result.cost_usd is None


@pytest.mark.parametrize(
    "provider,usage",
    [
        ("openai", TokenUsage(input_tokens=10, output_tokens=1, cached_input_tokens=11)),
        (
            "openai",
            TokenUsage(input_tokens=10, output_tokens=1, cached_input_tokens=0, total_tokens=99),
        ),
        (
            "openai",
            TokenUsage(
                input_tokens=10, output_tokens=1, cached_input_tokens=0, reasoning_output_tokens=2
            ),
        ),
        (
            "openai",
            TokenUsage(
                input_tokens=10, output_tokens=1, cached_input_tokens=0, cache_write_input_tokens=3
            ),
        ),
        ("foundry", claude_usage(cache_write_input_tokens=31)),
        ("foundry", claude_usage(cache_write_input_tokens=0)),
    ],
)
def test_inconsistent_usage_is_unavailable_instead_of_creating_negative_or_partial_bills(
    provider, usage
):
    result = account_usage(provider, usage, PRICES)
    assert result.status == "invalid_usage" and result.cost_usd is None


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity", "not-a-number"])
def test_invalid_prices_never_produce_money(price):
    result = account_usage("foundry", claude_usage(), {**PRICES, "input_usd_per_million": price})
    assert result.cost_usd is None and result.status == "invalid_pricing"


def test_decimal_rounding_is_explicit_and_tiny_amounts_are_not_rounded_to_cents():
    usage = TokenUsage(input_tokens=1, output_tokens=0, cached_input_tokens=0)
    prices = {**PRICES, "input_usd_per_million": "0.00015"}
    result = account_usage("openai", usage, prices)
    assert result.cost_usd == Decimal("0.0000000002")
    assert Decimal(result.components["input"]) == Decimal("0.00000000015")


def test_amount_exceeding_database_precision_is_explicitly_unavailable():
    result = account_usage("foundry", claude_usage(), {**PRICES, "input_usd_per_million": "1e20"})
    assert result.cost_usd is None and result.status == "cost_out_of_range"


def test_no_call_is_the_only_automatic_zero_without_usage_or_prices():
    result = account_usage(None, None, None, no_call=True)
    assert result.input_tokens == result.output_tokens == result.cost_usd == 0
    assert result.status == "no_call"


def test_real_zero_usage_can_be_priced_zero():
    result = account_usage("openai", TokenUsage(input_tokens=0, output_tokens=0), PRICES)
    assert result.cost_usd == 0 and result.status == "calculated"


@pytest.mark.parametrize("provider", ["foundry", "anthropic"])
@pytest.mark.parametrize("duration", ["5m", "1h"])
@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity"])
def test_invalid_optional_cache_prices_rejected(tmp_path, provider, duration, value):
    with pytest.raises(ValueError, match="Cache write prices"):
        Settings(
            tmp_path / "unused.db",
            **{f"{provider}_cache_write_{duration}_usd_per_million": Decimal(value)},
        )


def test_cache_write_prices_load_from_environment_and_preserve_explicit_zero(monkeypatch):
    monkeypatch.setenv("FOUNDRY_CACHE_WRITE_5M_USD_PER_MILLION", "0")
    monkeypatch.setenv("FOUNDRY_CACHE_WRITE_1H_USD_PER_MILLION", "3.75")
    for name, value in (("INPUT", "1"), ("CACHED_INPUT", "0.1"), ("OUTPUT", "2")):
        monkeypatch.setenv(f"FOUNDRY_{name}_USD_PER_MILLION", value)
    settings = Settings.from_environment()
    prices = settings.pricing_for(DEFAULT_ANTHROPIC_MODEL, "foundry")
    assert prices["source"] == "configuration"
    assert prices["cache_write_5m_usd_per_million"] == "0"
    assert prices["cache_write_1h_usd_per_million"] == "3.75"


def test_custom_claude_base_prices_do_not_infer_cache_write_rates(tmp_path):
    settings = Settings(
        tmp_path / "unused.db",
        anthropic_model="custom-deployment",
        anthropic_input_usd_per_million=Decimal("1"),
        anthropic_cached_input_usd_per_million=Decimal("0.1"),
        anthropic_output_usd_per_million=Decimal("2"),
    )
    prices = settings.pricing_for("custom-deployment", "anthropic")
    assert "cache_write_5m_usd_per_million" not in prices
    assert "cache_write_1h_usd_per_million" not in prices
