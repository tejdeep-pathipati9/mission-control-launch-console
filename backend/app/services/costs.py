from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DecimalException, localcontext

from app.llm.client import TokenUsage

USD_QUANTUM = Decimal("0.0000000001")
MAX_USD = Decimal("99999999.9999999999")
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Accounting:
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    status: str
    components: dict[str, str] | None = None


def account_usage(
    provider: str | None,
    usage: TokenUsage | None,
    pricing: dict[str, str] | None,
    *,
    no_call: bool = False,
) -> Accounting:
    """Calculate token-only USD estimates; never infer unreported usage or cache prices.

    OpenAI input includes cache hits. Claude base input excludes both cache reads and writes.
    Reasoning tokens are already included in output tokens and are never added again.
    """
    if no_call:
        return Accounting(0, 0, Decimal(0), "no_call")
    inputs = usage.input_tokens if usage else None
    outputs = usage.output_tokens if usage else None
    writes = usage.cache_write_input_tokens if usage else None
    if provider in {"anthropic", "foundry"} and usage:
        if writes is None and None not in (
            usage.cache_write_5m_input_tokens,
            usage.cache_write_1h_input_tokens,
        ):
            writes = usage.cache_write_5m_input_tokens + usage.cache_write_1h_input_tokens
        counts = (inputs, usage.cached_input_tokens, writes)
        inputs = sum(counts) if all(count is not None for count in counts) else None

    def unavailable(status):
        return Accounting(inputs, outputs, None, status)

    if provider == "fake":
        return unavailable("simulated")
    if usage is None or usage.input_tokens is None or outputs is None:
        return unavailable("usage_unavailable")
    if inputs is None:
        return unavailable("cache_usage_unavailable")
    if usage.reasoning_output_tokens is not None and usage.reasoning_output_tokens > outputs:
        return unavailable("invalid_usage")
    if usage.total_tokens is not None and usage.total_tokens != inputs + outputs:
        return unavailable("invalid_usage")

    cached = usage.cached_input_tokens
    if provider == "openai":
        if cached is None:
            if inputs:
                return unavailable("cache_usage_unavailable")
            cached = 0  # An empty total input cannot contain cache hits.
        if cached > inputs or any(
            count not in (None, 0)
            for count in (
                usage.cache_write_input_tokens,
                usage.cache_write_5m_input_tokens,
                usage.cache_write_1h_input_tokens,
            )
        ):
            return unavailable("invalid_usage")
        buckets = {"input": inputs - cached, "cached_input": cached, "output": outputs}
    elif provider in {"anthropic", "foundry"}:
        five, hour = usage.cache_write_5m_input_tokens, usage.cache_write_1h_input_tokens
        if writes == 0 and five in (None, 0) and hour in (None, 0):
            five = hour = 0
        elif five is None or hour is None:
            return unavailable("cache_usage_unavailable")
        if five + hour != writes:
            return unavailable("invalid_usage")
        buckets = {
            "input": usage.input_tokens,
            "cached_input": cached,
            "cache_write_5m": five,
            "cache_write_1h": hour,
            "output": outputs,
        }
    else:
        return unavailable("pricing_unavailable")
    if pricing is None:
        return unavailable("pricing_unavailable")
    try:
        with localcontext() as context:
            context.prec = 50
            components = {}
            for name, count in buckets.items():
                if not count:
                    components[name] = Decimal(0)
                    continue
                rate = pricing.get(f"{name}_usd_per_million")
                if rate is None:
                    return unavailable(
                        "cache_pricing_unavailable"
                        if name.startswith("cache")
                        else "pricing_unavailable"
                    )
                price = Decimal(rate)
                if not price.is_finite() or price < 0:
                    return unavailable("invalid_pricing")
                components[name] = Decimal(count) * price / MILLION
            amount = sum(components.values(), Decimal(0))
            if amount > MAX_USD:
                return unavailable("cost_out_of_range")
            amount = amount.quantize(USD_QUANTUM, rounding=ROUND_HALF_UP)
    except (DecimalException, OverflowError, TypeError, ValueError):
        return unavailable("invalid_pricing")
    return Accounting(
        inputs,
        outputs,
        amount,
        "calculated",
        {name: str(value) for name, value in components.items()},
    )
