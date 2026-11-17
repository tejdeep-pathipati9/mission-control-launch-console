from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIRECTORY = Path(__file__).resolve().parents[1] / "static"
router = APIRouter(include_in_schema=False)


@router.get("/")
def submission_page() -> FileResponse:
    return FileResponse(
        STATIC_DIRECTORY / "index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
        },
    )
