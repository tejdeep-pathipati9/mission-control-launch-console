import asyncio

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import BriefingVersion, CheckResult, PipelineRun, Report, TraceStep
from app.schemas.reports import ReportCreate
from app.services.briefings import review_briefing
from app.services.classification import classify_report
from app.services.reports import create_report, describe_report
from app.services.safety_checks import run_safety_checks
from tests.briefing_fake import ReviewClient


@pytest.fixture
async def prepared(tmp_path, payload):
    settings = Settings(tmp_path / "reports.db", model_timeout_seconds=0.05)
    database = Database(settings.database_path)
    database.initialize()
    with database.sessions.begin() as session:
        report_id, run_id = create_report(session, ReportCreate(**{**payload, "wind_knots": 26}))
    fake = ReviewClient()
    await classify_report(database, report_id, run_id, fake, settings)
    assert await run_safety_checks(database, report_id, run_id, fake, settings)
    yield database, settings, report_id, run_id
    database.close()


@pytest.mark.parametrize("mode", ["missing", "error"])
async def test_incomplete_checks_prevent_any_draft_call(prepared, mode):
    database, settings, report_id, run_id = prepared
    with database.sessions.begin() as session:
        check = session.scalar(select(CheckResult).where(CheckResult.check_type == "injection"))
        if mode == "missing":
            session.delete(check)
        else:
            check.outcome = "error"
    fake = ReviewClient()
    assert (
        await review_briefing(database, report_id, run_id, fake, settings)
        == "required_check_failed"
    )
    assert not fake.calls


@pytest.mark.parametrize("operation", ["draft", "evaluation"])
async def test_each_briefing_call_has_its_own_timeout(prepared, operation):
    database, settings, report_id, run_id = prepared

    class BlockedClient(ReviewClient):
        async def generate(self, request):
            if request.operation == operation:
                await asyncio.Event().wait()
            return await super().generate(request)

    assert await review_briefing(database, report_id, run_id, BlockedClient(), settings) == (
        f"{operation}_failed"
    )
    with database.sessions() as session:
        step = session.scalar(select(TraceStep).where(TraceStep.step_name == operation))
        assert step.status == "failed" and step.details_json["error_reason"] == "timeout"


async def test_missing_client_records_a_zero_cost_failed_draft(prepared):
    database, settings, report_id, run_id = prepared
    assert await review_briefing(database, report_id, run_id, None, settings) == "draft_failed"
    with database.sessions() as session:
        step = session.scalar(select(TraceStep).where(TraceStep.step_name == "draft"))
        assert step.input_tokens == step.output_tokens == step.cost_usd == 0
        assert step.details_json["error_reason"] == "model_not_configured"


async def test_polling_sees_unreviewed_draft_and_restart_preserves_it(prepared):
    database, settings, report_id, run_id = prepared
    entered = asyncio.Event()

    class InterruptedClient(ReviewClient):
        async def generate(self, request):
            if request.operation == "evaluation":
                entered.set()
                await asyncio.Event().wait()
            return await super().generate(request)

    task = asyncio.create_task(
        review_briefing(
            database,
            report_id,
            run_id,
            InterruptedClient(),
            Settings(settings.database_path, model_timeout_seconds=10),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with database.sessions() as session:
            detail = describe_report(session, session.get(Report, report_id))
            assert detail.status == "processing"
            assert not detail.latest_briefing.quality_passed
            assert detail.latest_briefing.evaluator_feedback is None
            assert detail.trace[-1].status == "processing"
            content = detail.latest_briefing.content
        # The awaiting model call holds no write transaction; recovery can acquire a lock.
        database.recover_interrupted_runs()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    with database.sessions() as session:
        detail = describe_report(session, session.get(Report, report_id))
        assert detail.current_run.failure_reason == "interrupted"
        assert detail.latest_briefing.content == content
        assert not detail.latest_briefing.quality_passed
        assert detail.trace[-1].status == "interrupted"
        assert detail.approvals == []


@pytest.mark.parametrize("terminated", [False, True])
async def test_late_model_response_cannot_promote_a_stale_or_terminated_run(prepared, terminated):
    database, settings, report_id, run_id = prepared

    class LateClient(ReviewClient):
        async def generate(self, request):
            result = await super().generate(request)
            if request.operation == "evaluation":
                with database.sessions.begin() as session:
                    if terminated:
                        session.get(PipelineRun, run_id).status = "failed"
                        session.get(Report, report_id).status = "failed"
                    else:
                        session.add(PipelineRun(report_id=report_id))
            return result

    assert await review_briefing(database, report_id, run_id, LateClient(), settings) is None
    with database.sessions() as session:
        assert session.get(Report, report_id).status == ("failed" if terminated else "processing")
        assert not session.scalar(select(BriefingVersion)).quality_passed


async def test_finished_briefing_is_not_processed_again(prepared):
    database, settings, report_id, run_id = prepared
    fake = ReviewClient()
    assert await review_briefing(database, report_id, run_id, fake, settings) is None
    calls = len(fake.calls)
    assert await review_briefing(database, report_id, run_id, fake, settings) is None
    assert len(fake.calls) == calls
    with database.sessions() as session:
        assert session.get(PipelineRun, run_id).status == "pending_review"
