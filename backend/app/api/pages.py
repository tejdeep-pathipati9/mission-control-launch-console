from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

STATIC_DIRECTORY = Path(__file__).resolve().parents[1] / "static"
router = APIRouter(include_in_schema=False)


def html_page(filename: str) -> FileResponse:
    return FileResponse(
        STATIC_DIRECTORY / filename,
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


def valid_report_id(value: str | None) -> bool:
    return bool(
        value
        and len(value) <= 16
        and value.isascii()
        and value.isdecimal()
        and not value.startswith("0")
        and int(value) <= 9007199254740991
    )


@router.get("/")
def submission_page(request: Request) -> Response:
    legacy_id = request.query_params.get("report")
    if valid_report_id(legacy_id):
        return RedirectResponse(f"/reports/{legacy_id}", status_code=307)
    return html_page("index.html")


@router.get("/reports/{report_id}")
def report_page(report_id: str) -> FileResponse:
    if not valid_report_id(report_id):
        raise HTTPException(404, "Report page not found.")
    return html_page("report.html")


@router.get("/director")
def director_page(request: Request) -> RedirectResponse:
    report_id = request.query_params.get("report")
    target = f"/reports/{report_id}" if valid_report_id(report_id) else "/reports"
    return RedirectResponse(target, status_code=307)


@router.get("/reports")
def report_queue(request: Request) -> Response:
    report_id = request.query_params.get("report")
    if valid_report_id(report_id):
        return RedirectResponse(f"/reports/{report_id}", status_code=307)
    return html_page("queue.html")
