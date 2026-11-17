import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import PipelineRun, Report, TraceStep, utc_now
from app.llm.client import ModelClient
from app.services.classification import classify_report

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
            run.cost_complete = False
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


def classification_pipeline(client: ModelClient | None, settings: Settings) -> PipelineRunner:
    async def process(database: Database, report_id: int, run_id: int) -> None:
        await classify_report(database, report_id, run_id, client, settings)
        # Later branches add required checks and review before making a report approvable.
        fail_run(
            database,
            report_id,
            run_id,
            "pipeline_not_implemented",
            "Classification saved. Concurrent checks and briefing review are not implemented yet.",
        )

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
