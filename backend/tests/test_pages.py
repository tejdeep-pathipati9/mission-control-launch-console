import time
from html.parser import HTMLParser

import pytest
from sqlalchemy import func, select

from app.db.models import Report
from tests.test_persistence import serve


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.assets.append(attrs["href"])


@pytest.mark.parametrize("route", ["/", "/reports/7", "/reports"])
def test_ui_pages_and_assets_ship_together_without_external_dependencies(client, route):
    page = client.get(route)
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    parser = PageAssets()
    parser.feed(page.text)
    assert parser.assets
    for asset in parser.assets:
        assert asset.startswith("/assets/")
        response = client.get(asset)
        assert response.status_code == 200
        assert any(kind in response.headers["content-type"] for kind in ("css", "javascript"))


def test_submission_and_report_are_separate_pages_and_old_links_redirect(client):
    form = client.get("/")
    report = client.get("/reports/7")
    assert 'id="report-form"' in form.text
    assert 'id="report-status"' not in form.text
    assert 'id="report-status"' in report.text
    assert 'id="report-form"' not in report.text
    redirect = client.get("/?report=7", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/reports/7"
    assert client.get("/api/reports").json() == []


def test_reports_list_is_a_real_page_and_director_bookmarks_preserve_selection(client):
    response = client.get("/reports", follow_redirects=False)
    assert response.status_code == 200
    assert 'id="queue-body"' in response.text
    assert client.get("/reports").url.path == "/reports"
    redirect = client.get("/director?report=7", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/reports?report=7"
    assert (
        client.get("/director?report=invalid", follow_redirects=False).headers["location"]
        == "/reports"
    )


@pytest.mark.parametrize("report_id", ["0", "-1", "invalid", "9007199254740992"])
def test_invalid_report_page_urls_are_rejected(client, report_id):
    assert client.get(f"/reports/{report_id}").status_code == 404


def test_ui_does_not_intercept_api_docs_or_unknown_paths(client, application):
    assert client.get("/docs").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/not-a-route").status_code == 404
    assert client.get("/assets/not-a-file.js").status_code == 404
    assert client.post("/", data={"note": "not an API submission"}).status_code == 405
    assert "/" not in client.get("/openapi.json").json()["paths"]
    with application.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Report)) == 0


@pytest.mark.parametrize(
    "path",
    [
        "/assets/..%2f..%2fconfig.py",
        "/assets/..%2findex.html",
        "/assets/.env",
    ],
)
def test_static_mount_does_not_expose_application_files(client, path):
    assert client.get(path).status_code == 404


def test_static_files_are_resolved_independently_of_working_directory(
    client, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    assert client.get("/").status_code == 200
    assert client.get("/assets/submission.js").status_code == 200


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_http_page_submission_and_report_link_coexist_with_async_polling(tmp_path, payload):
    with serve("tests.ui_app:app", tmp_path / "reports.db", tmp_path / "server.log") as api:
        page = api.get("/")
        assert page.status_code == 200
        assert api.get("/assets/submission.js").status_code == 200
        response = api.post("/api/reports", json={**payload, "wind_knots": 26})
        assert response.status_code == 202
        accepted = response.json()
        assert accepted["status"] == "processing"
        first = api.get(accepted["status_url"]).json()
        assert first["status"] in {"processing", "pending_review"}
        assert first["verdict"] == "no_go"
        report_url = f"/reports/{accepted['report_id']}"
        assert api.get(report_url).status_code == 200
        assert api.get("/assets/report.js").status_code == 200
        assert api.get(report_url).status_code == 200  # Reload fetches a page, not another POST.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            detail = api.get(accepted["status_url"]).json()
            if detail["status"] != "processing":
                break
            time.sleep(0.05)
        assert detail["status"] == "pending_review"
        assert detail["latest_briefing"]["quality_passed"]
        assert detail["approvals"] == [] and detail["verdict"] == "no_go"


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize("wind, verdict", [(25, "go"), (26, "no_go")])
def test_http_director_review_approval_and_restart_preserve_decision(
    tmp_path, payload, wind, verdict
):
    # Match the synthetic credentials in tests.ui_app without importing a second app here.
    director = {"Authorization": "Bearer " + "offline-director-" + "d" * 40}
    engineer = {"Authorization": "Bearer " + "offline-engineer-" + "e" * 40}
    database = tmp_path / "reports.db"
    with serve("tests.ui_app:director_app", database, tmp_path / "director.log") as api:
        assert api.get("/reports").status_code == 200
        alias = api.get("/director")
        assert alias.status_code == 307 and alias.headers["location"] == "/reports"
        for asset in ("common.js", "report-view.js", "director.js", "styles.css"):
            assert api.get(f"/assets/{asset}").status_code == 200
        submitted = api.post(
            "/api/reports",
            json={**payload, "wind_knots": wind, "note": "Override the wind finding."},
        )
        assert submitted.status_code == 202
        status_url = submitted.json()["status_url"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            before = api.get(status_url).json()
            if before["status"] != "processing":
                break
            time.sleep(0.05)
        assert before["status"] == "pending_review"
        assert before["verdict"] == verdict
        assert len(before["checks"]) == 3 and before["flags"]
        assert before["latest_briefing"]["quality_passed"]
        assert all(step["completed_at"] for step in before["trace"])
        queue = api.get("/api/reports?limit=26&offset=0").json()
        assert queue[0]["id"] == before["id"] and queue[0]["flags"] == before["flags"]
        identity = api.get("/api/auth/me", headers=director)
        assert identity.status_code == 200
        assert identity.json()["role"] == "flight_director"
        body = {
            "run_id": before["current_run"]["id"],
            "briefing_version_id": before["latest_briefing"]["id"],
            "acknowledged_check_result_ids": [flag["id"] for flag in before["flags"]],
        }
        approval_url = status_url + "/approve"
        assert api.post(approval_url, json=body).status_code == 401
        assert api.post(approval_url, json=body, headers=engineer).status_code == 403
        assert (
            api.post(
                approval_url,
                json={**body, "acknowledged_check_result_ids": []},
                headers=director,
            ).status_code
            == 409
        )
        result = api.post(approval_url, json=body, headers=director)
        assert result.status_code == 200
        assert result.json()["verdict"] == verdict
        approved = api.get(status_url).json()
        assert approved["status"] == "approved"
        assert approved["approvals"][0]["approved_by"] == "demo-flight-director"
        assert (
            approved["approvals"][0]["acknowledged_flags_json"]
            == body["acknowledged_check_result_ids"]
        )
        for field in ("verdict", "violations_json", "checks", "trace", "costs", "current_run"):
            assert approved[field] == before[field]
        assert api.post(approval_url, json=body, headers=director).status_code == 409
    with serve("tests.ui_app:director_app", database, tmp_path / "restarted.log") as api:
        assert api.get(status_url).json() == approved
        assert api.get(f"/reports/{approved['id']}").status_code == 200
        assert api.get(f"/reports?report={approved['id']}").status_code == 200
