from sqlalchemy import select

from app.config import Settings
from app.db.models import PipelineRun, TraceStep
from app.llm.client import ModelResponse
from app.services.costs import account_usage


def record_model_accounting(
    step: TraceStep, response: ModelResponse | None, settings: Settings, *, no_call: bool
) -> None:
    provider = response.provider if response else None
    usage = response.usage if response else None
    pricing = settings.pricing_for(response.model, provider) if response else None
    result = account_usage(provider, usage, pricing, no_call=no_call)
    step.input_tokens = result.input_tokens
    step.output_tokens = result.output_tokens
    step.cost_usd = result.cost_usd
    step.details_json = {
        **step.details_json,
        "usage": usage.model_dump() if usage else None,
        "usage_status": "no_call"
        if no_call
        else (
            "reported"
            if result.input_tokens is not None and result.output_tokens is not None
            else "unavailable_or_partial"
        ),
        "pricing": pricing,
        "input_token_basis": "base_plus_cache"
        if provider in {"anthropic", "foundry"}
        else "reported_total",
        "cost_status": result.status,
        "cost_components_usd": result.components,
        "cost_rounding": "ROUND_HALF_UP to 10 decimal places",
    }


def refresh_totals(session, run: PipelineRun) -> None:
    session.flush()
    steps = list(session.scalars(select(TraceStep).where(TraceStep.run_id == run.id)))
    for name, source in (
        ("total_input_tokens", "input_tokens"),
        ("total_output_tokens", "output_tokens"),
        ("total_cost_usd", "cost_usd"),
    ):
        values = [getattr(step, source) for step in steps]
        setattr(run, name, sum(values) if all(value is not None for value in values) else None)
    run.cost_complete = run.status != "processing" and run.total_cost_usd is not None
