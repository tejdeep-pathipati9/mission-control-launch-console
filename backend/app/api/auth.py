from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.auth import Principal, authenticated_principal
from app.schemas.auth import IdentityRead

router = APIRouter(prefix="/api/auth", tags=["Local demo authentication"])


@router.get("/me", response_model=IdentityRead)
def current_identity(
    principal: Annotated[Principal, Depends(authenticated_principal)], response: Response
) -> IdentityRead:
    response.headers["Cache-Control"] = "no-store"
    return IdentityRead(subject=principal.subject, role=principal.role)
