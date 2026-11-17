import json
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.auth import Principal, require_director
from app.config import Settings
from app.main import create_app

DIRECTOR_TOKEN = "synthetic-director-token-" + "d" * 32
ENGINEER_TOKEN = "synthetic-engineer-token-" + "e" * 32


@pytest.fixture
def auth_api(tmp_path):
    settings = Settings(
        tmp_path / "reports.db",
        demo_director_token=DIRECTOR_TOKEN,
        demo_engineer_token=ENGINEER_TOKEN,
    )
    application = create_app(settings)

    @application.get("/test/director-only")
    def restricted(principal: Annotated[Principal, Depends(require_director)]):
        return {"subject": principal.subject}

    with TestClient(application) as api:
        yield api


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer incorrect"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        {"X-Role": "flight_director", "X-User": "Director"},
    ],
)
def test_missing_or_invalid_authentication_is_401_without_identity_leak(auth_api, headers):
    response = auth_api.get("/api/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert DIRECTOR_TOKEN not in response.text and ENGINEER_TOKEN not in response.text
    assert auth_api.get("/test/director-only", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "token,subject,role",
    [
        (DIRECTOR_TOKEN, "demo-flight-director", "flight_director"),
        (ENGINEER_TOKEN, "demo-engineer", "engineer"),
    ],
)
def test_bearer_token_maps_to_server_owned_identity(auth_api, token, subject, role):
    response = auth_api.get(
        "/api/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Role": "flight_director",
            "X-User": "Forged",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"subject": subject, "role": role, "auth_mode": "local_demo"}
    assert response.headers["cache-control"] == "no-store"
    assert token not in response.text


def test_engineer_identity_cannot_elevate_to_director(auth_api):
    response = auth_api.get(
        "/test/director-only",
        headers={
            "Authorization": f"Bearer {ENGINEER_TOKEN}",
            "X-Role": "flight_director",
        },
    )
    assert response.status_code == 403
    assert auth_api.get(
        "/test/director-only",
        headers={
            "Authorization": f"Bearer {DIRECTOR_TOKEN}",
        },
    ).json() == {"subject": "demo-flight-director"}


def test_absent_auth_configuration_has_no_default_director_access(client):
    assert (
        client.get(
            "/api/auth/me",
            headers={
                "Authorization": f"Bearer {DIRECTOR_TOKEN}",
            },
        ).status_code
        == 401
    )


@pytest.mark.parametrize("token", ["short", " " * 32, "a" * 31, "a" * 257, "a" * 32 + "\n"])
def test_invalid_configured_tokens_fail_without_exposing_values(tmp_path, token):
    with pytest.raises(ValueError, match="DEMO_DIRECTOR_TOKEN") as error:
        Settings(tmp_path / "reports.db", demo_director_token=token)
    assert token not in str(error.value)


def test_identity_configuration_is_distinct_and_hidden_in_repr(tmp_path):
    with pytest.raises(ValueError, match="must be different"):
        Settings(
            tmp_path / "reports.db",
            demo_director_token=DIRECTOR_TOKEN,
            demo_engineer_token=DIRECTOR_TOKEN,
        )
    settings = Settings(tmp_path / "reports.db", demo_director_token=DIRECTOR_TOKEN)
    assert DIRECTOR_TOKEN not in repr(settings)


def test_demo_tokens_load_privately_from_environment(monkeypatch):
    monkeypatch.setenv("DEMO_DIRECTOR_TOKEN", DIRECTOR_TOKEN)
    monkeypatch.setenv("DEMO_ENGINEER_TOKEN", ENGINEER_TOKEN)
    settings = Settings.from_environment()
    assert settings.demo_director_token == DIRECTOR_TOKEN
    assert settings.demo_engineer_token == ENGINEER_TOKEN
    assert DIRECTOR_TOKEN not in repr(settings) and ENGINEER_TOKEN not in repr(settings)


def test_openapi_describes_bearer_auth_without_secrets(auth_api):
    schema = auth_api.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["DemoBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/api/auth/me"]["get"]["security"] == [{"DemoBearer": []}]
    assert DIRECTOR_TOKEN not in json.dumps(schema) and ENGINEER_TOKEN not in json.dumps(schema)
