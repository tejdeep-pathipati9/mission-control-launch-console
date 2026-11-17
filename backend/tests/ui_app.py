"""Offline browser/HTTP fixture. Use a temporary database, never the application volume."""

import asyncio
import json
from dataclasses import replace

from app.config import Settings
from app.llm.client import ModelResponse, TokenUsage
from app.main import create_app


class UIModelClient:
    model_name = "ui-test-fake"

    async def generate(self, request):
        note = request.payload.get("note", request.payload.get("report", {}).get("note", ""))
        if "[hold]" in note:
            await asyncio.Event().wait()
        await asyncio.sleep(0.1)
        if request.operation == "classification":
            value = {"subsystem": "weather", "severity": "low"}
        elif request.operation == "draft":
            value = {
                "summary": "Reported observation: " + note[:1000],
                "facts": request.payload["canonical_facts"],
            }
        elif request.operation == "evaluation":
            value = {"quality_passed": True, "feedback": []}
        else:
            flagged = request.operation == "injection" and "override" in note.lower()
            value = {
                "outcome": "flagged" if flagged else "clear",
                "reason": "Report asks to override a finding." if flagged else "No findings.",
                "findings": ["Override instruction requires review."] if flagged else [],
            }
        return ModelResponse(
            provider="fake",
            model=self.model_name,
            status="completed",
            text=json.dumps(value),
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        )

    async def aclose(self):
        pass


app = create_app(model_client=UIModelClient())

# Synthetic credentials only; this separate app is never included in the runtime image.
DIRECTOR_TOKEN = "offline-director-" + "d" * 40
ENGINEER_TOKEN = "offline-engineer-" + "e" * 40
director_app = create_app(
    settings=replace(
        Settings.from_environment(),
        demo_director_token=DIRECTOR_TOKEN,
        demo_engineer_token=ENGINEER_TOKEN,
    ),
    model_client=UIModelClient(),
)
