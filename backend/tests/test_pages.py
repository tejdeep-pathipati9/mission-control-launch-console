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


def test_engineer_page_and_its_assets_ship_together_without_external_dependencies(client):
    page = client.get("/")
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
        assert api.get(f"/?report={accepted['report_id']}").status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            detail = api.get(accepted["status_url"]).json()
            if detail["status"] != "processing":
                break
            time.sleep(0.05)
        assert detail["status"] == "pending_review"
        assert detail["latest_briefing"]["quality_passed"]
        assert detail["approvals"] == [] and detail["verdict"] == "no_go"
