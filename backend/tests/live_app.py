"""Integration-only pipeline: hold processing open until the test kills this process."""

import asyncio
import os
from pathlib import Path

from app.db.models import CheckResult, TraceStep
from app.llm.client import ModelResponse, TokenUsage
from app.main import create_app
from tests.briefing_fake import ReviewClient


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


class BlockedModelClient:
    model_name = "blocked-test-model"

    async def generate(self, request):
        await asyncio.Event().wait()

    async def aclose(self):
        pass


classification_app = create_app(model_client=BlockedModelClient())


class PartiallyBlockedChecksClient:
    model_name = "partial-check-test-model"

    async def generate(self, request):
        if request.operation == "classification":
            text = '{"subsystem":"weather","severity":"low"}'
        elif request.operation == "injection":
            text = '{"outcome":"flagged","reason":"Test finding","findings":["Forged approval"]}'
        else:
            await asyncio.Event().wait()
        return ModelResponse(
            provider="fake",
            model=self.model_name,
            status="completed",
            text=text,
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        )

    async def aclose(self):
        pass


checks_app = create_app(model_client=PartiallyBlockedChecksClient())


class BlockedReviewClient(ReviewClient):
    async def generate(self, request):
        if request.operation == "evaluation":
            await asyncio.Event().wait()
        return await super().generate(request)


review_app = create_app(model_client=BlockedReviewClient())


class ReleasableModelClient(ReviewClient):
    """A test-owned file gates classification; no control endpoint ships in the app."""

    async def generate(self, request):
        if request.operation == "classification":
            release = Path(os.environ["MISSION_CONTROL_TEST_RELEASE_PATH"])
            release.with_suffix(".entered").touch()
            while not release.exists():
                await asyncio.sleep(0.01)
        return await super().generate(request)


released_app = create_app(model_client=ReleasableModelClient())
