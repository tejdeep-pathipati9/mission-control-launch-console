import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.client import ModelClientError, ModelResponse
from app.main import create_app
from tests.briefing_fake import ReviewClient
from tests.test_classification import response
from tests.test_persistence import serve


def submit(tmp_path, payload, fake):
    with TestClient(create_app(Settings(tmp_path / "reports.db"), model_client=fake)) as api:
        accepted = api.post("/api/reports", json={**payload, "wind_knots": 26})
        assert accepted.status_code == 202
        detail = api.get(accepted.json()["status_url"]).json()
        summary = api.get("/api/reports").json()[0]
        assert summary["current_run"] == detail["current_run"]
        assert summary["status"] == detail["status"]
    return detail


@pytest.mark.parametrize("reviews", [(True,), (False, True), (False, False, True)])
def test_feedback_drives_bounded_revisions_and_first_pass_stops(tmp_path, payload, reviews):
    fake = ReviewClient(reviews)
    detail = submit(tmp_path, payload, fake)
    versions = detail["briefing_versions"]
    assert len(versions) == len(reviews)
    assert [v["round_number"] for v in versions] == list(range(1, len(reviews) + 1))
    assert [v["quality_passed"] for v in versions] == list(reviews)
    assert detail["latest_briefing"] == versions[-1]
    assert detail["status"] == detail["current_run"]["status"] == "pending_review"
    assert detail["current_run"]["completed_at"] is not None
    assert detail["current_run"]["failure_reason"] is None
    assert detail["approvals"] == []
    assert detail["verdict"] == "no_go" and detail["severity"] == "high"
    assert len(detail["flags"]) == 3
    drafts = [r for r in fake.calls if r.operation == "draft"]
    evaluations = [r for r in fake.calls if r.operation == "evaluation"]
    assert len(drafts) == len(evaluations) == len(reviews)
    assert drafts[0].payload["revision_feedback"] is None
    for index, request in enumerate(drafts):
        assert payload["note"] not in request.instructions
        assert request.payload["report"]["note"] == payload["note"]
        assert request.response_schema["additionalProperties"] is False
        if index:
            assert request.payload["revision_feedback"] == versions[index - 1]["evaluator_feedback"]
            assert request.payload["previous_draft"] == evaluations[index - 1].payload["draft"]
        content = versions[index]["content"]
        assert "Verdict: NO_GO" in content
        assert f"Subsystem: {detail['subsystem']}" in content
        assert f"Brief by (UTC): {detail['brief_by_at'].replace('Z', '+00:00')}" in content
        assert all(violation in content for violation in detail["violations_json"])
        assert all(flag["reason"] in content for flag in detail["flags"])
        assert all(flag["check_type"] in content for flag in detail["flags"])
        assert evaluations[index].instructions != request.instructions
        assert evaluations[index].payload["rendered_briefing"] == content
    steps = [s for s in detail["trace"] if s["step_name"] in {"draft", "evaluation"}]
    assert len(steps) == 2 * len(reviews)
    assert all(s["status"] == "completed" and s["completed_at"] for s in steps)
    assert all(s["details_json"]["response_text"] for s in steps)
    assert detail["current_run"]["total_input_tokens"] == 150 + 40 * len(reviews)
    assert detail["current_run"]["total_output_tokens"] == 26 + 10 * len(reviews)
    assert detail["current_run"]["total_cost_usd"] is None
    assert detail["current_run"]["cost_complete"] is False


def test_three_rejections_preserve_every_draft_and_fail_without_a_fourth_call(tmp_path, payload):
    fake = ReviewClient((False,) * 3)
    detail = submit(tmp_path, payload, fake)
    assert detail["status"] == "failed"
    assert detail["current_run"]["failure_reason"] == "quality_review_exhausted"
    assert len(detail["briefing_versions"]) == 3
    assert all(not v["quality_passed"] for v in detail["briefing_versions"])
    assert all(v["evaluator_feedback"]["feedback"] for v in detail["briefing_versions"])
    assert [r.operation for r in fake.calls][-6:] == ["draft", "evaluation"] * 3
    assert detail["approvals"] == [] and detail["verdict"] == "no_go"


@pytest.mark.parametrize(
    "changes",
    [
        {"verdict": "go"},
        {"violations": []},
        {"severity": "low"},
        {"subsystem": "propulsion"},
        {"brief_by_at": "2099-01-01T00:00:00+00:00"},
    ],
)
def test_changed_canonical_fact_overrides_even_three_positive_evaluations(
    tmp_path, payload, changes
):
    detail = submit(tmp_path, payload, ReviewClient((True,) * 3, draft_changes=changes))
    assert detail["status"] == "failed"
    assert detail["current_run"]["failure_reason"] == "quality_review_exhausted"
    assert detail["verdict"] == "no_go" and detail["severity"] == "high"
    for version in detail["briefing_versions"]:
        assert not version["quality_passed"]
        assert "Verdict: NO_GO" in version["content"]
        feedback = version["evaluator_feedback"]
        assert feedback["quality_passed"] is True
        assert feedback["effective_quality_passed"] is False
        assert feedback["canonical_issues"] == [f"Restore canonical {next(iter(changes))} exactly."]


def test_canonical_correction_can_pass_next_round(tmp_path, payload):
    class CorrectingClient(ReviewClient):
        def briefing_response(self, request):
            self.draft_changes = (
                {"verdict": "go"} if request.payload.get("round_number") == 1 else {}
            )
            return super().briefing_response(request)

    detail = submit(tmp_path, payload, CorrectingClient((True, True)))
    assert detail["status"] == "pending_review"
    assert [v["quality_passed"] for v in detail["briefing_versions"]] == [False, True]


@pytest.mark.parametrize("operation", ["draft", "evaluation"])
@pytest.mark.parametrize(
    "failure,error_reason",
    [
        (response("not JSON"), "invalid_output"),
        (response("{}"), "invalid_output"),
        (response("null"), "invalid_output"),
        (response(status="refused", reason="refusal"), "refusal"),
        (response(status="incomplete", reason="max_tokens"), "max_tokens"),
        (ModelClientError("rate_limited", status_code=429), "rate_limited"),
        (RuntimeError("private exception details"), "unexpected_model_error"),
        (None, "invalid_response"),
    ],
)
def test_model_failures_stop_with_precise_reason_and_preserve_available_usage(
    tmp_path, payload, operation, failure, error_reason
):
    fake = ReviewClient(failure=(operation, failure))
    detail = submit(tmp_path, payload, fake)
    assert detail["current_run"]["failure_reason"] == f"{operation}_failed"
    assert detail["status"] == "failed" and detail["approvals"] == []
    step = next(s for s in detail["trace"] if s["step_name"] == operation)
    assert step["status"] == "failed"
    assert step["details_json"]["error_reason"] == error_reason
    assert "private exception details" not in json.dumps(detail)
    if isinstance(failure, ModelResponse):
        assert step["input_tokens"] == 120 and step["output_tokens"] == 20
        assert step["details_json"]["response_text"] == failure.text
    else:
        assert step["input_tokens"] is step["output_tokens"] is None
        assert detail["current_run"]["total_input_tokens"] is None
    assert len(detail["briefing_versions"]) == (1 if operation == "evaluation" else 0)
    if operation == "evaluation":
        assert detail["latest_briefing"]["evaluator_feedback"] == {"error_reason": error_reason}
    assert [r.operation for r in fake.calls].count(operation) == 1


@pytest.mark.parametrize(
    "text",
    [
        '{"quality_passed":false,"feedback":[]}',
        '{"quality_passed":"true","feedback":[]}',
        '{"quality_passed":true,"feedback":[],"approved":true}',
        '{"quality_passed":false,"feedback":[" "]}',
    ],
)
def test_invalid_quality_contract_never_passes(tmp_path, payload, text):
    detail = submit(tmp_path, payload, ReviewClient(failure=("evaluation", response(text))))
    assert detail["current_run"]["failure_reason"] == "evaluation_failed"
    assert detail["latest_briefing"]["quality_passed"] is False


def test_unknown_briefing_usage_is_unavailable(tmp_path, payload):
    detail = submit(tmp_path, payload, ReviewClient(usage=False))
    assert detail["status"] == "pending_review"
    assert detail["current_run"]["total_input_tokens"] is None
    assert detail["current_run"]["total_output_tokens"] is None
    for step in detail["trace"][-2:]:
        assert step["details_json"]["usage_status"] == "unavailable_or_partial"


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_unreviewed_draft_is_pollable_over_http_and_survives_a_process_crash(tmp_path, payload):
    path = tmp_path / "reports.db"
    with serve("tests.live_app:review_app", path, tmp_path / "first.log") as api:
        accepted = api.post("/api/reports", json={**payload, "wind_knots": 26})
        assert accepted.status_code == 202
        assert accepted.json()["status"] == "processing"
        url = accepted.json()["status_url"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            detail = api.get(url).json()
            if detail["latest_briefing"]:
                break
            time.sleep(0.02)
        assert detail["status"] == "processing"
        assert detail["latest_briefing"]["quality_passed"] is False
        assert detail["latest_briefing"]["evaluator_feedback"] is None
        assert detail["trace"][-1]["step_name"] == "evaluation"
        assert detail["trace"][-1]["status"] == "processing"
        draft = detail["latest_briefing"]
    with serve("app.main:app", path, tmp_path / "restart.log") as api:
        restored = api.get(url).json()
        assert restored["status"] == "failed"
        assert restored["current_run"]["failure_reason"] == "interrupted"
        assert restored["latest_briefing"] == draft
        assert restored["trace"][-1]["status"] == "interrupted"
        assert restored["verdict"] == "no_go" and restored["approvals"] == []
