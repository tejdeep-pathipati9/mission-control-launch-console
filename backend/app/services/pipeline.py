import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import PipelineRun, Report, TraceStep, utc_now
from app.llm.client import ModelClient
from app.services.briefings import review_briefing
from app.services.classification import classify_report
from app.services.safety_checks import run_safety_checks
from app.services.trace import refresh_totals

logger = logging.getLogger(__name__)
PipelineRunner = Callable[[Database, int, int], Awaitable[None]]


def fail_run(database: Database, report_id: int, run_id: int, reason: str, message: str) -> None:
    with database.sessions.begin() as session:
        run = session.get(PipelineRun, run_id)
        if run is None or run.report_id != report_id or run.status != "processing":
            return
        now = utc_now()
        run.status = "failed"
        run.failure_reason = reason
        run.failure_message = message
        run.completed_at = now
        latest_id = session.scalar(
            select(PipelineRun.id)
            .where(PipelineRun.report_id == report_id)
            .order_by(PipelineRun.id.desc())
            .limit(1)
        )
        if latest_id == run_id:
            session.get(Report, report_id).status = "failed"
        for step in session.scalars(
            select(TraceStep).where(TraceStep.run_id == run_id, TraceStep.status == "processing")
        ):
            step.status = "failed"
            step.completed_at = now
            step.cost_usd = None
            step.details_json = {**step.details_json, "cost_status": "usage_unavailable"}
        session.add(
            TraceStep(
                run_id=run_id,
                step_name="pipeline",
                status="failed",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
                details_json={"failure_reason": reason, "message": message},
                started_at=now,
                completed_at=now,
            )
        )
        refresh_totals(session, run)


def report_pipeline(client: ModelClient | None, settings: Settings) -> PipelineRunner:
    async def process(database: Database, report_id: int, run_id: int) -> None:
        await classify_report(database, report_id, run_id, client, settings)
        checks_passed = await run_safety_checks(database, report_id, run_id, client, settings)
        if checks_passed is None:
            return
        if not checks_passed:
            fail_run(
                database,
                report_id,
                run_id,
                "required_check_failed",
                "A required check could not be completed; inspect its error and trace.",
            )
            return
        failure = await review_briefing(database, report_id, run_id, client, settings)
        if failure:
            messages = {
                "required_check_failed": "Required checks must complete before briefing review.",
                "draft_failed": "Briefing generation failed; inspect the draft trace.",
                "evaluation_failed": "Briefing evaluation failed; inspect the evaluation trace.",
                "quality_review_exhausted": "No briefing passed quality review after three rounds.",
            }
            fail_run(database, report_id, run_id, failure, messages[failure])

    return process


async def execute_pipeline(
    database: Database, report_id: int, run_id: int, runner: PipelineRunner
) -> None:
    try:
        await runner(database, report_id, run_id)
        fail_run(
            database,
            report_id,
            run_id,
            "processing_incomplete",
            "Processing returned without producing a complete result.",
        )
    except Exception:
        logger.exception("Pipeline failed for report %s, run %s", report_id, run_id)
        fail_run(database, report_id, run_id, "processing_error", "The processing pipeline failed.")
