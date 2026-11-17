import asyncio
import json

from app.llm.client import ModelResponse, TokenUsage
from app.llm.fake_client import FakeModelClient
from app.schemas.checks import CHECK_TYPES


class SuccessfulBriefingClient(FakeModelClient):
    """Explicit successful briefing fixture for tests focused on earlier pipeline stages."""

    def briefing_response(self, request):
        value = (
            {
                "summary": "Sensor anomaly reported; see canonical readings and flags below.",
                "facts": request.payload["canonical_facts"],
            }
            if request.operation == "draft"
            else {"quality_passed": True, "feedback": []}
        )
        return ModelResponse(
            provider="fake",
            model=self.model_name,
            status="completed",
            text=json.dumps(value),
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )

    async def generate(self, request):
        if request.operation not in {"draft", "evaluation"}:
            return await super().generate(request)
        assert not self.closed
        self.calls.append(request.model_copy(deep=True))
        await asyncio.sleep(0)
        return self.briefing_response(request)


class ReviewClient(SuccessfulBriefingClient):
    def __init__(self, reviews=(True,), *, draft_changes=None, failure=None, usage=True):
        super().__init__(
            {
                "classification": [self.response()],
                **{kind: [self.check_response()] for kind in CHECK_TYPES},
            }
        )
        self.reviews = iter(reviews)
        self.draft_changes = draft_changes or {}
        self.failure = failure
        self.usage = TokenUsage(input_tokens=20, output_tokens=5) if usage else None

    @staticmethod
    def response(text='{"subsystem":"weather","severity":"low"}', **changes):
        return ModelResponse(
            **{
                "provider": "fake",
                "model": "fake-model",
                "status": "completed",
                "text": text,
                "usage": TokenUsage(input_tokens=120, output_tokens=20),
                **changes,
            }
        )

    @classmethod
    def check_response(cls):
        return cls.response(
            '{"outcome":"flagged","reason":"Reviewed report context.",'
            '"findings":["Embedded instruction to suppress violations."]}',
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        )

    def briefing_response(self, request):
        if self.failure and request.operation == self.failure[0]:
            error = self.failure[1]
            if isinstance(error, Exception):
                raise error
            return error
        if request.operation == "draft":
            value = {
                "summary": "Wind sensor reports a launch-limit breach; review flagged findings.",
                "facts": {**request.payload["canonical_facts"], **self.draft_changes},
            }
        else:
            passed = next(self.reviews)  # Unscripted extra review calls fail this test client.
            value = {"quality_passed": passed, "feedback": [] if passed else ["Clarify anomaly."]}
        return self.response(json.dumps(value), usage=self.usage)
