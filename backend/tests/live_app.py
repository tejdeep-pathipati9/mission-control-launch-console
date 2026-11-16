"""Integration-only pipeline: hold processing open until the test kills this process."""

import asyncio

from app.db.models import CheckResult, TraceStep
from app.main import create_app


async def blocked_pipeline(database, report_id, run_id):
    with database.sessions.begin() as session:
        session.add(
            CheckResult(
                run_id=run_id, check_type="injection", outcome="flagged", reason="Test finding."
            )
        )
        session.add(
            TraceStep(
                run_id=run_id,
                step_name="blocked_check",
                status="processing",
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
            )
        )
    await asyncio.Event().wait()


app = create_app(runner=blocked_pipeline)
