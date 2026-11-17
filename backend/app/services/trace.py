from sqlalchemy import select

from app.db.models import PipelineRun, TraceStep


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
    run.cost_complete = run.total_cost_usd is not None
