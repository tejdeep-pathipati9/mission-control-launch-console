import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.database import Database
from app.db.models import BriefingVersion, CheckResult, PipelineRun, Report, TraceStep
from app.main import create_app
from app.schemas.reports import ReportCreate
from app.services.reports import create_report


def test_independent_databases_are_isolated(tmp_path, payload):
    with TestClient(create_app(Settings(tmp_path / "one.db"))) as first:
        first.post("/api/reports", json=payload)
        assert len(first.get("/api/reports").json()) == 1
    with TestClient(create_app(Settings(tmp_path / "two.db"))) as second:
        assert second.get("/api/reports").json() == []
    with TestClient(create_app(Settings(tmp_path / "one.db"))) as restarted:
        assert len(restarted.get("/api/reports").json()) == 1


def test_schema_foreign_keys_and_constraints(client, application, payload):
    client.post("/api/reports", json=payload)
    database = application.state.database
    assert set(inspect(database.engine).get_table_names()) == {
        "reports",
        "pipeline_runs",
        "check_results",
        "trace_steps",
        "briefing_versions",
        "approvals",
    }
    with database.sessions() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(
            CheckResult(
                run_id=9999, check_type="injection", outcome="clear", reason="Invalid parent."
            )
        )
    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(
            CheckResult(run_id=1, check_type="other", outcome="clear", reason="Invalid type")
        )
    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(
            CheckResult(run_id=1, check_type="panic", outcome="unknown", reason="Bad outcome")
        )
    with database.sessions.begin() as session:
        session.add(CheckResult(run_id=1, check_type="panic", outcome="clear", reason="First"))
    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(CheckResult(run_id=1, check_type="panic", outcome="clear", reason="Duplicate"))
    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(
            BriefingVersion(report_id=1, run_id=1, round_number=4, content="Too many rounds")
        )


def test_recovery_preserves_completed_trace_and_does_not_rewrite_other_runs(tmp_path, payload):
    database = Database(tmp_path / "reports.db")
    database.initialize()
    with database.sessions.begin() as session:
        report_id, old_run_id = create_report(session, ReportCreate(**payload))
        # A historical interrupted attempt must not demote a newer completed run.
        newer = PipelineRun(report_id=report_id, status="pending_review")
        session.add(newer)
        session.get(Report, report_id).status = "pending_review"
    database.recover_interrupted_runs()
    with database.sessions() as session:
        assert session.get(PipelineRun, old_run_id).failure_reason == "interrupted"
        assert session.get(PipelineRun, newer.id).status == "pending_review"
        assert session.get(Report, report_id).status == "pending_review"
        rule = session.scalar(select(TraceStep).where(TraceStep.run_id == old_run_id))
        assert rule.status == "completed"
        assert rule.input_tokens == 0
    database.close()


@contextmanager
def serve(module, database_path, log_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {**os.environ, "MISSION_CONTROL_DB_PATH": str(database_path)}
    environment.pop("OPENAI_API_KEY", None)
    with log_path.open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
            cwd=Path(__file__).parents[1],
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False
            ) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        if client.get("/api/health").status_code == 200:
                            yield client
                            return
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                log.seek(0)
                pytest.fail(f"Server failed to start: {log.read()}")
        finally:
            if process.poll() is None:
                process.kill()  # Deliberate crash, not graceful shutdown; tests startup recovery.
            process.wait(timeout=10)


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_real_http_returns_202_while_pipeline_is_blocked_and_recovers_after_crash(
    tmp_path, payload
):
    database_path = tmp_path / "reports.db"
    with serve("tests.live_app:app", database_path, tmp_path / "first.log") as client:
        response = client.post("/api/reports", json={**payload, "wind_knots": 26})
        assert response.status_code == 202
        status_url = response.json()["status_url"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            before = client.get(status_url).json()
            if before["checks"]:
                break
            time.sleep(0.02)
        assert before["status"] == "processing"
        assert before["checks"][0]["outcome"] == "flagged"
        assert before["trace"][-1]["status"] == "processing"
        assert before["trace"][-1]["input_tokens"] is None
    with serve("app.main:app", database_path, tmp_path / "restarted.log") as client:
        after = client.get(status_url).json()
        assert after["status"] == "failed"
        assert after["current_run"]["failure_reason"] == "interrupted"
        assert after["current_run"]["completed_at"] is not None
        assert after["current_run"]["cost_complete"] is False
        assert after["verdict"] == before["verdict"] == "no_go"
        assert after["note"] == before["note"]
        assert after["checks"] == before["checks"]
        assert after["trace"][0] == before["trace"][0]
        assert after["trace"][-1]["status"] == "interrupted"
        assert after["trace"][-1]["completed_at"] is not None
