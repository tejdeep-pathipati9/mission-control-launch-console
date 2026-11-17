import asyncio
import json
from decimal import Decimal

import httpx2 as httpx
import pytest
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient

from app.config import DEFAULT_ANTHROPIC_MODEL, Settings
from app.db.database import Database
from app.db.models import PipelineRun, Report
from app.llm.anthropic_client import AnthropicModelClient
from app.llm.client import ModelClientError, TokenUsage
from app.main import create_app
from app.schemas.reports import ReportCreate
from app.services.classification import classify_report
from app.services.pipeline import execute_pipeline, fail_run, report_pipeline
from app.services.reports import create_report, describe_report
from tests.briefing_fake import ReviewClient

PER_CALL = Decimal("0.0010225000")
RAW_USAGE = {
    "input_tokens": 100,
    "output_tokens": 10,
    "cache_read_input_tokens": 20,
    "cache_creation_input_tokens": 30,
    "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
}
USAGE = TokenUsage(
    input_tokens=100,
    output_tokens=10,
    cached_input_tokens=20,
    cache_write_input_tokens=30,
    cache_write_5m_input_tokens=10,
    cache_write_1h_input_tokens=20,
)


def settings_for(path, *, priced=True, **changes):
    return Settings(
        **{
            "database_path": path,
            "model_provider": "foundry",
            "anthropic_model": "assignment-deployment",
            "anthropic_foundry_api_key": "synthetic-test-key",
            "anthropic_foundry_resource": "synthetic-test-resource",
            **(
                {
                    "foundry_input_usd_per_million": Decimal("5"),
                    "foundry_cached_input_usd_per_million": Decimal("0.5"),
                    "foundry_output_usd_per_million": Decimal("25"),
                    "foundry_cache_write_5m_usd_per_million": Decimal("6.25"),
                    "foundry_cache_write_1h_usd_per_million": Decimal("10"),
                }
                if priced
                else {}
            ),
            **changes,
        }
    )


class CostClient(ReviewClient):
    """Synthetic provider-labeled replies to exercise accounting without sending requests."""

    model_name = "assignment-deployment"

    async def generate(self, request):
        result = await super().generate(request)
        return result.model_copy(
            update={
                "provider": "foundry",
                "model": DEFAULT_ANTHROPIC_MODEL,
                "usage": USAGE,
            }
        )


@pytest.mark.parametrize("reviews", [(True,), (False, False, True), (False, False, False)])
async def test_full_pipeline_prices_actual_sdk_usage_and_returns_complete_api_totals(
    tmp_path, payload, reviews
):
    settings = settings_for(tmp_path / "reports.db")
    results = iter(reviews)
    operations = []

    def reply(request):
        body = json.loads(request.content)
        fields = body["output_config"]["format"]["schema"]["properties"]
        context = json.loads(body["messages"][0]["content"])
        if "summary" in fields:
            operation = "draft"
            value = {
                "summary": "Wind reading exceeds its limit; see flagged findings.",
                "facts": context["canonical_facts"],
            }
        elif "quality_passed" in fields:
            operation = "evaluation"
            passed = next(results)
            value = {"quality_passed": passed, "feedback": [] if passed else ["Clarify anomaly."]}
        elif "subsystem" in fields:
            operation = "classification"
            value = {"subsystem": "weather", "severity": "low"}
        else:
            operation = "check"
            value = {
                "outcome": "flagged",
                "reason": "Human review required.",
                "findings": ["Unverified handover instruction."],
            }
        operations.append(operation)
        return httpx.Response(
            200,
            json={
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "model": DEFAULT_ANTHROPIC_MODEL,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "content": [{"type": "text", "text": json.dumps(value)}],
                "usage": RAW_USAGE,
            },
        )

    sdk = AsyncAnthropic(
        api_key="synthetic-test-key",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(reply),
            trust_env=False,
        ),
    )
    client = AnthropicModelClient(settings, sdk_client=sdk)
    try:
        with TestClient(create_app(settings, model_client=client)) as api:
            accepted = api.post("/api/reports", json={**payload, "wind_knots": 26})
            assert accepted.status_code == 202
            detail = api.get(accepted.json()["status_url"]).json()
            summary = api.get("/api/reports").json()[0]
        calls = 4 + 2 * len(reviews)
        assert len(operations) == calls
        assert detail["status"] == ("pending_review" if reviews[-1] else "failed")
        assert detail["verdict"] == "no_go" and detail["approvals"] == []
        assert detail["current_run"]["cost_complete"] is True
        assert Decimal(detail["current_run"]["total_cost_usd"]) == PER_CALL * calls
        assert detail["current_run"]["total_input_tokens"] == 150 * calls
        assert detail["current_run"]["total_output_tokens"] == 10 * calls
        assert Decimal(detail["current_run"]["known_cost_usd"]) == PER_CALL * calls
        assert detail["costs"] == summary["costs"]
        assert Decimal(detail["costs"]["total_cost_usd"]) == PER_CALL * calls
        assert detail["costs"]["cost_complete"] is True
        for step in detail["trace"]:
            assert step["latency_ms"] >= 0
            if step["step_name"] in {"launch_limits", "pipeline"}:
                assert Decimal(step["cost_usd"]) == 0
                continue
            assert step["input_tokens"] == 150
            assert step["details_json"]["usage"]["input_tokens"] == 100
            assert step["details_json"]["cost_status"] == "calculated"
            assert step["details_json"]["pricing"]["model"] == DEFAULT_ANTHROPIC_MODEL
            assert step["details_json"]["pricing"]["deployment"] == "assignment-deployment"
            assert Decimal(step["cost_usd"]) == PER_CALL
        assert sum(Decimal(step["cost_usd"]) for step in detail["trace"]) == PER_CALL * calls
    finally:
        await client.aclose()


@pytest.mark.parametrize("missing", ["base_prices", "cache_prices", "usage"])
def test_missing_accounting_information_keeps_totals_unavailable(tmp_path, payload, missing):
    settings = settings_for(
        tmp_path / "reports.db",
        priced=missing != "base_prices",
        **({"foundry_cache_write_1h_usd_per_million": None} if missing == "cache_prices" else {}),
    )

    class MissingClient(CostClient):
        async def generate(self, request):
            result = await super().generate(request)
            if missing == "usage" and request.operation == "evaluation":
                return result.model_copy(update={"usage": None})
            return result

    with TestClient(create_app(settings, model_client=MissingClient())) as api:
        accepted = api.post("/api/reports", json=payload).json()
        detail = api.get(accepted["status_url"]).json()
    assert detail["status"] == "pending_review"
    assert detail["current_run"]["total_cost_usd"] is detail["costs"]["total_cost_usd"] is None
    assert not detail["current_run"]["cost_complete"] and not detail["costs"]["cost_complete"]
    assert Decimal(detail["costs"]["known_cost_usd"]) == (PER_CALL * 5 if missing == "usage" else 0)
    last = detail["trace"][-1]
    assert (
        last["details_json"]["cost_status"]
        == {
            "base_prices": "pricing_unavailable",
            "cache_prices": "cache_pricing_unavailable",
            "usage": "usage_unavailable",
        }[missing]
    )


@pytest.mark.parametrize("operation", ["classification", "injection", "draft", "evaluation"])
def test_refused_or_invalid_responses_still_record_their_reported_cost(
    tmp_path, payload, operation
):
    class RefusingClient(CostClient):
        async def generate(self, request):
            result = await super().generate(request)
            if request.operation == operation:
                return result.model_copy(update={"status": "refused", "text": None})
            return result

    with TestClient(
        create_app(settings_for(tmp_path / "reports.db"), model_client=RefusingClient())
    ) as api:
        accepted = api.post("/api/reports", json=payload).json()
        detail = api.get(accepted["status_url"]).json()
    failed_step = next(step for step in detail["trace"] if step["step_name"] == operation)
    assert Decimal(failed_step["cost_usd"]) == PER_CALL
    assert failed_step["details_json"]["cost_status"] == "calculated"
    assert detail["current_run"]["cost_complete"] is True
    assert Decimal(detail["current_run"]["total_cost_usd"]) == sum(
        Decimal(step["cost_usd"]) for step in detail["trace"]
    )


def test_transport_error_leaves_known_subtotal_but_not_a_fabricated_full_cost(tmp_path, payload):
    fake = CostClient(failure=("evaluation", ModelClientError("connection_error")))
    with TestClient(create_app(settings_for(tmp_path / "reports.db"), model_client=fake)) as api:
        accepted = api.post("/api/reports", json=payload).json()
        detail = api.get(accepted["status_url"]).json()
    assert detail["current_run"]["failure_reason"] == "evaluation_failed"
    assert detail["current_run"]["total_cost_usd"] is None
    assert detail["current_run"]["total_input_tokens"] is None
    assert not detail["costs"]["cost_complete"]
    assert Decimal(detail["costs"]["known_cost_usd"]) == PER_CALL * 5


async def test_partial_polling_and_restart_preserve_completed_costs_without_marking_them_complete(
    tmp_path, payload
):
    settings = settings_for(tmp_path / "reports.db")
    database = Database(settings.database_path)
    database.initialize()
    with database.sessions.begin() as session:
        report_id, run_id = create_report(session, ReportCreate(**payload))
    entered = asyncio.Event()

    class BlockedClient(CostClient):
        async def generate(self, request):
            if request.operation == "evaluation":
                entered.set()
                await asyncio.Event().wait()
            return await super().generate(request)

    task = asyncio.create_task(
        execute_pipeline(
            database,
            report_id,
            run_id,
            report_pipeline(BlockedClient(), settings),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with database.sessions() as session:
            detail = describe_report(session, session.get(Report, report_id))
            assert detail.status == "processing"
            assert detail.current_run.total_cost_usd is None
            assert detail.costs.known_cost_usd == PER_CALL * 5
            assert not detail.costs.cost_complete
            assert detail.trace[-1].latency_ms is None
        database.recover_interrupted_runs()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        database.close()
    with TestClient(create_app(settings)) as api:
        restored = api.get(f"/api/reports/{report_id}").json()
        assert restored["status"] == "failed"
        assert restored["current_run"]["failure_reason"] == "interrupted"
        assert Decimal(restored["costs"]["known_cost_usd"]) == PER_CALL * 5
        assert restored["costs"]["total_cost_usd"] is None
        assert not restored["costs"]["cost_complete"]
        assert restored["trace"][-1]["details_json"]["cost_status"] == "interrupted"


@pytest.mark.parametrize("first_unknown", [False, True])
async def test_report_totals_cover_all_runs_and_saved_prices_survive_configuration_changes(
    tmp_path, payload, first_unknown
):
    settings = settings_for(tmp_path / "reports.db")
    database = Database(settings.database_path)
    database.initialize()
    try:
        with database.sessions.begin() as session:
            report_id, first_id = create_report(session, ReportCreate(**payload))

        class FirstClient(CostClient):
            async def generate(self, request):
                if first_unknown:
                    raise ModelClientError("timeout")
                return await super().generate(request)

        await classify_report(database, report_id, first_id, FirstClient(), settings)
        fail_run(database, report_id, first_id, "test_failure", "Synthetic first attempt.")
        with database.sessions.begin() as session:
            session.get(Report, report_id).status = "processing"
            second = PipelineRun(report_id=report_id)
            session.add(second)
            session.flush()
            second_id = second.id
        # Doubling only the base-input rate changes each subsequent call by 0.0005 USD.
        later_settings = settings_for(
            settings.database_path, foundry_input_usd_per_million=Decimal("10")
        )
        await execute_pipeline(
            database, report_id, second_id, report_pipeline(CostClient(), later_settings)
        )
    finally:
        database.close()
    # GET must not recalculate either run using today's (here, missing) prices.
    with TestClient(create_app(Settings(settings.database_path))) as api:
        detail = api.get(f"/api/reports/{report_id}").json()
        summary = api.get("/api/reports").json()[0]
    expected_current = (PER_CALL + Decimal("0.0005")) * 6
    assert Decimal(detail["current_run"]["total_cost_usd"]) == expected_current
    if first_unknown:
        assert detail["pipeline_runs"][0]["total_cost_usd"] is None
        assert detail["costs"]["total_cost_usd"] is None
        assert detail["costs"]["total_input_tokens"] is None
        assert not detail["costs"]["cost_complete"]
    else:
        assert Decimal(detail["pipeline_runs"][0]["total_cost_usd"]) == PER_CALL
        assert Decimal(detail["costs"]["total_cost_usd"]) == PER_CALL + expected_current
        assert detail["costs"]["total_input_tokens"] == 150 * 7
        assert detail["costs"]["cost_complete"] is True
    assert Decimal(detail["costs"]["known_cost_usd"]) == (
        expected_current + (0 if first_unknown else PER_CALL)
    )
    assert detail["costs"] == summary["costs"]
    assert all(step["run_id"] == second_id for step in detail["trace"])


async def test_processing_totals_are_provisional_even_between_completed_calls(tmp_path, payload):
    settings = settings_for(tmp_path / "reports.db")
    database = Database(settings.database_path)
    database.initialize()
    try:
        with database.sessions.begin() as session:
            report_id, run_id = create_report(session, ReportCreate(**payload))
        await classify_report(database, report_id, run_id, CostClient(), settings)
        with database.sessions() as session:
            detail = describe_report(session, session.get(Report, report_id))
            assert detail.current_run.total_cost_usd == PER_CALL
            assert not detail.current_run.cost_complete
            assert not detail.costs.cost_complete
    finally:
        database.close()
