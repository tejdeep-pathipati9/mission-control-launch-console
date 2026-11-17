from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Approval,
    BriefingVersion,
    CheckResult,
    PipelineRun,
    Report,
    TraceStep,
    utc_now,
)
from app.schemas.reports import (
    ApprovalRead,
    BriefingRead,
    CheckRead,
    ReportCosts,
    ReportCreate,
    ReportDetail,
    ReportSummary,
    RunRead,
    TraceRead,
)
from app.services.launch_rules import calculate_brief_by, evaluate_launch_limits


def create_report(session: Session, payload: ReportCreate) -> tuple[int, int]:
    decision = evaluate_launch_limits(payload.wind_knots, payload.temp_c, payload.fuel_pct)
    now = utc_now()
    report = Report(
        **payload.model_dump(),
        verdict=decision.verdict,
        violations_json=list(decision.violations),
        severity="high" if decision.violations else None,
        brief_by_at=calculate_brief_by(now, "high") if decision.violations else None,
        created_at=now,
        updated_at=now,
    )
    session.add(report)
    session.flush()
    run = PipelineRun(report_id=report.id, started_at=now)
    session.add(run)
    session.flush()
    session.add(
        TraceStep(
            run_id=run.id,
            step_name="launch_limits",
            status="completed",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0,
            details_json={"verdict": decision.verdict, "violations": list(decision.violations)},
            started_at=now,
            completed_at=utc_now(),
        )
    )
    return report.id, run.id


def read_runs(session: Session, report_id: int) -> list[RunRead]:
    runs = list(
        session.scalars(
            select(PipelineRun).where(PipelineRun.report_id == report_id).order_by(PipelineRun.id)
        )
    )
    subtotals = {run.id: Decimal(0) for run in runs}
    for run_id, cost in session.execute(
        select(TraceStep.run_id, TraceStep.cost_usd)
        .join(PipelineRun, TraceStep.run_id == PipelineRun.id)
        .where(PipelineRun.report_id == report_id, TraceStep.cost_usd.is_not(None))
    ):
        subtotals[run_id] += cost
    return [
        RunRead(
            **{
                field: getattr(run, field)
                for field in RunRead.model_fields
                if field != "known_cost_usd"
            },
            known_cost_usd=subtotals[run.id],
        )
        for run in runs
    ]


def report_costs(runs: list[RunRead]) -> ReportCosts:
    totals = {}
    for field in ("total_input_tokens", "total_output_tokens", "total_cost_usd"):
        values = [getattr(run, field) for run in runs]
        totals[field] = sum(values) if all(value is not None for value in values) else None
    return ReportCosts(
        **totals,
        known_cost_usd=sum((run.known_cost_usd for run in runs), Decimal(0)),
        cost_complete=bool(runs) and all(run.cost_complete for run in runs),
    )


def summarize_report(
    session: Session, report: Report, runs: list[RunRead] | None = None
) -> ReportSummary:
    runs = read_runs(session, report.id) if runs is None else runs
    run = runs[-1]
    flags = session.scalars(
        select(CheckResult)
        .where(CheckResult.run_id == run.id, CheckResult.outcome == "flagged")
        .order_by(CheckResult.id)
    )
    values = {
        field: getattr(report, field)
        for field in ReportSummary.model_fields
        if field not in {"current_run", "flags", "costs"}
    }
    return ReportSummary(
        **values,
        current_run=run,
        costs=report_costs(runs),
        flags=[CheckRead.model_validate(flag) for flag in flags],
    )


def describe_report(session: Session, report: Report) -> ReportDetail:
    runs = read_runs(session, report.id)
    summary = summarize_report(session, report, runs)
    run_id = summary.current_run.id
    checks = session.scalars(
        select(CheckResult).where(CheckResult.run_id == run_id).order_by(CheckResult.id)
    )
    steps = session.scalars(
        select(TraceStep).where(TraceStep.run_id == run_id).order_by(TraceStep.id)
    )
    versions = [
        BriefingRead.model_validate(version)
        for version in session.scalars(
            select(BriefingVersion)
            .where(BriefingVersion.run_id == run_id)
            .order_by(BriefingVersion.round_number)
        )
    ]
    approvals = session.scalars(
        select(Approval).where(Approval.report_id == report.id).order_by(Approval.id)
    )
    return ReportDetail(
        **summary.model_dump(),
        wind_knots=report.wind_knots,
        temp_c=report.temp_c,
        fuel_pct=report.fuel_pct,
        note=report.note,
        checks=[CheckRead.model_validate(check) for check in checks],
        trace=[TraceRead.model_validate(step) for step in steps],
        briefing_versions=versions,
        latest_briefing=versions[-1] if versions else None,
        approvals=[ApprovalRead.model_validate(approval) for approval in approvals],
        pipeline_runs=runs,
    )
