from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from sqlalchemy import select, text

from app.db.models import Report
from app.schemas.reports import ReportAccepted, ReportCreate, ReportDetail, ReportSummary
from app.services.pipeline import execute_pipeline, fail_run
from app.services.reports import create_report, describe_report, summarize_report

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    with request.app.state.database.sessions() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok", "stage": "concurrent_checks"}


@router.post("/reports", response_model=ReportAccepted, status_code=status.HTTP_202_ACCEPTED)
def submit_report(
    payload: ReportCreate, request: Request, response: Response, background_tasks: BackgroundTasks
) -> ReportAccepted:
    database = request.app.state.database
    with database.sessions.begin() as session:
        report_id, run_id = create_report(session, payload)
    try:
        background_tasks.add_task(
            execute_pipeline, database, report_id, run_id, request.app.state.pipeline_runner
        )
    except Exception as exc:
        fail_run(database, report_id, run_id, "scheduling_failed", "Could not schedule processing.")
        raise HTTPException(503, "Report saved, but processing could not be scheduled.") from exc
    status_url = f"/api/reports/{report_id}"
    response.headers["Location"] = status_url
    return ReportAccepted(report_id=report_id, status_url=status_url)


@router.get("/reports", response_model=list[ReportSummary])
def list_reports(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReportSummary]:
    with request.app.state.database.sessions() as session:
        reports = session.scalars(
            select(Report).order_by(Report.id.desc()).offset(offset).limit(limit)
        )
        return [summarize_report(session, report) for report in reports]


@router.get("/reports/{report_id}", response_model=ReportDetail)
def get_report(report_id: int, request: Request) -> ReportDetail:
    with request.app.state.database.sessions() as session:
        report = session.get(Report, report_id)
        if report is None:
            raise HTTPException(404, "Report not found.")
        return describe_report(session, report)
