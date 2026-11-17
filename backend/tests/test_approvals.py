from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from app.auth import Principal
from app.config import Settings
from app.db.models import (
    Approval,
    BriefingVersion,
    CheckResult,
    PipelineRun,
    Report,
    TraceStep,
    utc_now,
)
from app.main import create_app
from app.schemas.approvals import ApprovalRequest
from app.schemas.checks import CHECK_TYPES
from app.schemas.reports import ReportCreate
from app.services.approvals import ApprovalError, approve_briefing
from app.services.reports import create_report
from tests.briefing_fake import ReviewClient
from tests.test_auth import DIRECTOR_TOKEN, ENGINEER_TOKEN

HEADERS = {"Authorization": f"Bearer {DIRECTOR_TOKEN}"}


def app_settings(path):
    return Settings(path, demo_director_token=DIRECTOR_TOKEN, demo_engineer_token=ENGINEER_TOKEN)


def approval_body(detail):
    return {
        "run_id": detail["current_run"]["id"],
        "briefing_version_id": detail["latest_briefing"]["id"],
        "acknowledged_check_result_ids": [flag["id"] for flag in reversed(detail["flags"])],
    }


@pytest.fixture
def ready(tmp_path, payload):
    application = create_app(app_settings(tmp_path / "reports.db"), model_client=ReviewClient())
    with TestClient(application) as api:
        accepted = api.post("/api/reports", json={**payload, "wind_knots": 26}).json()
        detail = api.get(accepted["status_url"]).json()
        assert detail["status"] == "pending_review"
        yield api, application.state.database, detail


def approve(api, detail, body=None, headers=None, report_id=None):
    return api.post(
        f"/api/reports/{report_id if report_id is not None else detail['id']}/approve",
        json=approval_body(detail) if body is None else body,
        headers=HEADERS if headers is None else headers,
    )


def unchanged(api, database, detail):
    after = api.get(f"/api/reports/{detail['id']}").json()
    assert after == detail
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Approval)) == 0


@pytest.mark.parametrize("flagged", [False, True])
@pytest.mark.parametrize("wind", [25, 26])
def test_human_approval_preserves_verdict_flags_history_costs_and_survives_restart(
    tmp_path, payload, flagged, wind
):
    class ChecksClient(ReviewClient):
        @classmethod
        def check_response(cls):
            if flagged:
                return super().check_response()
            return cls.response('{"outcome":"clear","reason":"Complete check.","findings":[]}')

    settings = app_settings(tmp_path / "reports.db")
    fake = ChecksClient()
    with TestClient(create_app(settings, model_client=fake)) as api:
        accepted = api.post("/api/reports", json={**payload, "wind_knots": wind}).json()
        before = api.get(accepted["status_url"]).json()
        calls = len(fake.calls)
        assert before["status"] == "pending_review" and before["approvals"] == []
        response = approve(api, before)
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "approved"
        assert result["verdict"] == ("go" if wind == 25 else "no_go")
        audit = result["approval"]
        assert audit["approved_by"] == "demo-flight-director"
        assert audit["briefing_version_id"] == before["latest_briefing"]["id"]
        assert audit["acknowledged_flags_json"] == sorted(flag["id"] for flag in before["flags"])
        assert datetime.fromisoformat(audit["approved_at"]).utcoffset().total_seconds() == 0
        assert len(fake.calls) == calls  # A human action does not call the model.
        after = api.get(accepted["status_url"]).json()
        assert after["status"] == "approved" and after["approvals"] == [audit]
        assert api.get("/api/reports").json()[0]["status"] == "approved"
        for field in before:
            if field not in {"status", "updated_at", "approvals"}:
                assert after[field] == before[field]
        assert after["current_run"]["status"] == "pending_review"
        assert approve(api, before).status_code == 409
        assert api.get(accepted["status_url"]).json() == after
    with TestClient(create_app(settings)) as api:
        assert api.get(accepted["status_url"]).json() == after
        assert approve(api, before).status_code == 409


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({}, 401),
        ({"Authorization": "Bearer invalid"}, 401),
        ({"X-Role": "flight_director", "X-Approved-By": "Director"}, 401),
        ({"Authorization": f"Bearer {ENGINEER_TOKEN}", "X-Role": "flight_director"}, 403),
    ],
)
def test_server_authorization_blocks_missing_credentials_and_forged_roles(ready, headers, expected):
    api, database, detail = ready
    assert approve(api, detail, headers=headers).status_code == expected
    unchanged(api, database, detail)


def test_service_also_rejects_a_non_director(ready):
    api, database, detail = ready
    with pytest.raises(ApprovalError) as error:
        approve_briefing(
            database,
            detail["id"],
            ApprovalRequest(**approval_body(detail)),
            Principal("demo-engineer", "engineer"),
        )
    assert error.value.status_code == 403
    unchanged(api, database, detail)


def test_unknown_report_is_404_for_director_and_still_requires_auth(ready):
    api, database, detail = ready
    assert approve(api, detail, report_id=99999).status_code == 404
    assert approve(api, detail, report_id=99999, headers={}).status_code == 401
    unchanged(api, database, detail)


@pytest.mark.parametrize("state", ["processing", "failed", "approved"])
def test_only_pending_reports_can_be_approved(ready, state):
    api, database, detail = ready
    with database.sessions.begin() as session:
        session.get(Report, detail["id"]).status = state
    changed = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, detail).status_code == 409
    unchanged(api, database, changed)


@pytest.mark.parametrize("change", ["processing", "failed", "unfinished", "failure_reason"])
def test_run_must_be_finished_and_successful_even_if_report_says_pending(ready, change):
    api, database, detail = ready
    with database.sessions.begin() as session:
        run = session.get(PipelineRun, detail["current_run"]["id"])
        if change in {"processing", "failed"}:
            run.status = change
        elif change == "unfinished":
            run.completed_at = None
        else:
            run.failure_reason = "quality_review_exhausted"
    before = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, detail).json()["detail"]["code"] == "run_not_ready"
    unchanged(api, database, before)


def test_unresolved_processing_trace_blocks_approval(ready):
    api, database, detail = ready
    with database.sessions.begin() as session:
        session.add(
            TraceStep(
                run_id=detail["current_run"]["id"],
                step_name="evaluation",
                status="processing",
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
            )
        )
    before = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, detail).json()["detail"]["code"] == "processing_incomplete"
    unchanged(api, database, before)


def test_a_fail_safe_flag_with_an_evaluation_error_cannot_be_acknowledged_into_approval(ready):
    api, database, detail = ready
    with database.sessions.begin() as session:
        check = session.scalar(select(CheckResult).where(CheckResult.check_type == "injection"))
        check.details_json = {
            **check.details_json,
            "error_reason": "invalid_output",
            "review_required": True,
        }
    before = api.get(f"/api/reports/{detail['id']}").json()
    response = approve(api, before)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "checks_incomplete"
    unchanged(api, database, before)


@pytest.mark.parametrize("kind", CHECK_TYPES)
@pytest.mark.parametrize("mode", ["missing", "error"])
def test_missing_or_errored_required_check_blocks_approval(ready, kind, mode):
    api, database, detail = ready
    with database.sessions.begin() as session:
        check = session.scalar(select(CheckResult).where(CheckResult.check_type == kind))
        if mode == "missing":
            session.delete(check)
        else:
            check.outcome = "error"
    before = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, before).json()["detail"]["code"] == "checks_incomplete"
    unchanged(api, database, before)


@pytest.mark.parametrize("mode", ["missing", "failed_quality", "no_feedback"])
def test_briefing_must_exist_and_have_completed_quality_review(ready, mode):
    api, database, detail = ready
    with database.sessions.begin() as session:
        version = session.get(BriefingVersion, detail["latest_briefing"]["id"])
        if mode == "missing":
            session.delete(version)
        elif mode == "failed_quality":
            version.quality_passed = False
        else:
            version.evaluator_feedback = None
    before = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, detail).status_code == 409
    unchanged(api, database, before)


def test_earlier_passing_version_cannot_bypass_a_newer_failed_version(ready):
    api, database, detail = ready
    with database.sessions.begin() as session:
        session.add(
            BriefingVersion(
                report_id=detail["id"],
                run_id=detail["current_run"]["id"],
                round_number=2,
                content="New failed draft.",
                quality_passed=False,
            )
        )
    before = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, detail).json()["detail"]["code"] == "stale_briefing"
    assert approve(api, before).json()["detail"]["code"] == "quality_not_passed"
    unchanged(api, database, before)


@pytest.mark.parametrize("mode", ["missing_flag", "empty", "unknown", "nonflagged"])
def test_acknowledgements_must_match_exactly_the_current_flags(ready, mode):
    api, database, detail = ready
    body = approval_body(detail)
    if mode == "missing_flag":
        body["acknowledged_check_result_ids"].pop()
    elif mode == "empty":
        body["acknowledged_check_result_ids"] = []
    elif mode == "unknown":
        body["acknowledged_check_result_ids"][0] = 99999
    else:
        with database.sessions.begin() as session:
            session.get(CheckResult, body["acknowledged_check_result_ids"][0]).outcome = "clear"
        detail = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, detail, body).json()["detail"]["code"] == "acknowledgements_mismatch"
    unchanged(api, database, detail)


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": True},
        {"run_id": "1"},
        {"run_id": 0},
        {"run_id": 1.5},
        {"briefing_version_id": -1},
        {"acknowledged_check_result_ids": [1, 1]},
        {"acknowledged_check_result_ids": [True]},
        {"acknowledged_check_result_ids": ["1"]},
        {"acknowledged_check_result_ids": None},
        {"acknowledged_check_result_ids": [1, 2, 3, 4]},
        {"approved_by": "forged-director"},
        {"role": "flight_director"},
        {"verdict": "go"},
    ],
)
def test_malformed_or_forged_approval_fields_are_422(ready, changes):
    api, database, detail = ready
    assert approve(api, detail, {**approval_body(detail), **changes}).status_code == 422
    unchanged(api, database, detail)


@pytest.mark.parametrize(
    "field", ["run_id", "briefing_version_id", "acknowledged_check_result_ids"]
)
def test_approval_fields_are_explicitly_required(ready, field):
    api, database, detail = ready
    body = approval_body(detail)
    del body[field]
    assert approve(api, detail, body).status_code == 422
    unchanged(api, database, detail)


def test_stale_run_and_historical_flag_ids_cannot_approve_the_current_attempt(ready):
    api, database, detail = ready
    with database.sessions.begin() as session:
        run = PipelineRun(report_id=detail["id"], status="pending_review", completed_at=utc_now())
        session.add(run)
        session.flush()
        session.add(
            BriefingVersion(
                report_id=detail["id"],
                run_id=run.id,
                round_number=1,
                content="Current briefing.",
                quality_passed=True,
                evaluator_feedback={"quality_passed": True},
            )
        )
        for kind in CHECK_TYPES:
            session.add(
                CheckResult(run_id=run.id, check_type=kind, outcome="flagged", reason="New flag")
            )
    before = api.get(f"/api/reports/{detail['id']}").json()
    assert approve(api, detail).json()["detail"]["code"] == "stale_run"
    stale_version = {
        **approval_body(before),
        "briefing_version_id": detail["latest_briefing"]["id"],
    }
    assert approve(api, before, stale_version).json()["detail"]["code"] == "stale_briefing"
    stale_flags = {
        **approval_body(before),
        "acknowledged_check_result_ids": approval_body(detail)["acknowledged_check_result_ids"],
    }
    assert approve(api, before, stale_flags).json()["detail"]["code"] == "acknowledgements_mismatch"
    unchanged(api, database, before)
    assert approve(api, before).status_code == 200


def test_another_reports_version_and_flags_are_rejected(ready, payload):
    api, database, detail = ready
    with database.sessions.begin() as session:
        other_id, run_id = create_report(session, ReportCreate(**payload))
        version = BriefingVersion(
            report_id=other_id,
            run_id=run_id,
            round_number=1,
            content="Other briefing",
            quality_passed=True,
        )
        check = CheckResult(
            run_id=run_id, check_type="injection", outcome="flagged", reason="Other"
        )
        session.add_all([version, check])
        session.flush()
        version_id, flag_id = version.id, check.id
    body = {**approval_body(detail), "briefing_version_id": version_id}
    assert approve(api, detail, body).json()["detail"]["code"] == "stale_briefing"
    body = {**approval_body(detail), "acknowledged_check_result_ids": [flag_id]}
    assert approve(api, detail, body).json()["detail"]["code"] == "acknowledgements_mismatch"
    unchanged(api, database, detail)


def test_audit_failure_rolls_back_status_and_allows_a_clean_retry(ready):
    api, database, detail = ready

    def break_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO approvals"):
            raise OperationalError("Synthetic audit failure", {}, None)

    event.listen(database.engine, "before_cursor_execute", break_audit)
    try:
        assert approve(api, detail).status_code == 503
    finally:
        event.remove(database.engine, "before_cursor_execute", break_audit)
    unchanged(api, database, detail)
    assert approve(api, detail).status_code == 200


def test_simultaneous_approval_requests_have_exactly_one_winner(ready):
    api, database, detail = ready
    both_ready = Barrier(2)

    def coordinate(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE reports SET status="):
            both_ready.wait(timeout=5)

    event.listen(database.engine, "before_cursor_execute", coordinate)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: approve(api, detail).status_code, range(2)))
    finally:
        event.remove(database.engine, "before_cursor_execute", coordinate)
    assert sorted(results) == [200, 409]
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Approval)) == 1
        assert session.get(Report, detail["id"]).status == "approved"
        assert session.get(PipelineRun, detail["current_run"]["id"]).status == "pending_review"
