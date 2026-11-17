from dataclasses import dataclass
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

Role = Literal["flight_director", "engineer"]


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role


bearer = HTTPBearer(
    auto_error=False,
    scheme_name="DemoBearer",
    description="Local demo only. Server-configured tokens map to fixed identities and roles.",
)


def authenticated_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    settings = request.app.state.settings
    if credentials is not None:
        supplied = credentials.credentials.encode("utf-8")
        for token, principal in (
            (settings.demo_director_token, Principal("demo-flight-director", "flight_director")),
            (settings.demo_engineer_token, Principal("demo-engineer", "engineer")),
        ):
            if token is not None and compare_digest(supplied, token.encode("utf-8")):
                return principal
    raise HTTPException(
        status_code=401,
        detail="Valid demo bearer authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_director(
    principal: Annotated[Principal, Depends(authenticated_principal)],
) -> Principal:
    if principal.role != "flight_director":
        raise HTTPException(403, "Flight Director role required.")
    return principal
