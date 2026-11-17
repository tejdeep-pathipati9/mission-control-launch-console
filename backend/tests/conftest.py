import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_DIRECTOR_TOKEN", raising=False)
    monkeypatch.delenv("DEMO_ENGINEER_TOKEN", raising=False)


@pytest.fixture
def payload():
    return {
        "engineer_name": "Avery Chen",
        "station": "Weather",
        "wind_knots": 25,
        "temp_c": 4,
        "fuel_pct": 98,
        "note": "Wind sensor agrees with the backup sensor; monitoring gusts.",
    }


@pytest.fixture
def application(tmp_path):
    return create_app(Settings(tmp_path / "reports.db"))


@pytest.fixture
def client(application):
    with TestClient(application) as connection:
        yield connection
