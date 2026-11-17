from sqlalchemy import select, update

from app.auth import Principal
from app.db.database import Database
from app.db.models import (
    Approval,
    BriefingVersion,
    CheckResult,
    PipelineRun,
    Report,
    TraceStep,
    utc_now,
)
from app.schemas.approvals import ApprovalRequest, ApprovalResponse
from app.schemas.checks import CHECK_TYPES
from app.schemas.reports import ApprovalRead


class ApprovalError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def conflict(code: str, message: str) -> None:
    raise ApprovalError(409, code, message)


def approve_briefing(
    database: Database, report_id: int, payload: ApprovalRequest, principal: Principal
) -> ApprovalResponse:
    if principal.role != "flight_director":
        raise ApprovalError(403, "director_required", "Flight Director role required.")
    with database.sessions.begin() as session:
        now = utc_now()
        # Write before reading: acquire SQLite's writer lock without a deferred read snapshot.
        # The conditional update admits one winner; every validation failure rolls it back.
        claimed = session.scalar(
            update(Report)
            .where(Report.id == report_id, Report.status == "pending_review")
            .values(status="approved", updated_at=now)
            .returning(Report.id)
            .execution_options(synchronize_session=False)
        )
        report = session.get(Report, report_id)
        if report is None:
            raise ApprovalError(404, "report_not_found", "Report not found.")
        if claimed is None:
            conflict("report_not_pending", "Only a pending briefing can be approved.")

        run = session.scalar(
            select(PipelineRun)
            .where(PipelineRun.report_id == report_id)
            .order_by(PipelineRun.id.desc())
            .limit(1)
        )
        if run is None or run.id != payload.run_id:
            conflict("stale_run", "The requested processing attempt is not current.")
        if (
            run.status != "pending_review"
            or run.completed_at is None
            or run.failure_reason is not None
        ):
            conflict("run_not_ready", "Processing must complete successfully before approval.")
        if (
            session.scalar(
                select(TraceStep.id)
                .where(TraceStep.run_id == run.id, TraceStep.status == "processing")
                .limit(1)
            )
            is not None
        ):
            conflict("processing_incomplete", "A processing step is still unresolved.")

        checks = list(session.scalars(select(CheckResult).where(CheckResult.run_id == run.id)))
        if (
            len(checks) != 3
            or {check.check_type for check in checks} != set(CHECK_TYPES)
            or any(check.outcome not in {"clear", "flagged"} for check in checks)
        ):
            conflict("checks_incomplete", "All three required checks must complete without errors.")

        version = session.scalar(
            select(BriefingVersion)
            .where(BriefingVersion.run_id == run.id)
            .order_by(BriefingVersion.round_number.desc(), BriefingVersion.id.desc())
            .limit(1)
        )
        if (
            version is None
            or version.report_id != report_id
            or version.id != payload.briefing_version_id
        ):
            conflict("stale_briefing", "Approve the latest briefing of the current report attempt.")
        if not version.quality_passed or version.evaluator_feedback is None:
            conflict("quality_not_passed", "The latest briefing has not completed quality review.")
        if session.scalar(select(Approval.id).where(Approval.briefing_version_id == version.id)):
            conflict("already_approved", "This briefing has already been approved.")

        flags = {check.id for check in checks if check.outcome == "flagged"}
        if set(payload.acknowledged_check_result_ids) != flags:
            conflict(
                "acknowledgements_mismatch",
                "Acknowledge exactly the flagged check IDs from this processing attempt.",
            )
        approval = Approval(
            report_id=report_id,
            briefing_version_id=version.id,
            approved_by=principal.subject,
            acknowledged_flags_json=sorted(flags),
            approved_at=now,
        )
        session.add(approval)
        session.flush()
        result = ApprovalResponse(
            report_id=report_id,
            run_id=run.id,
            verdict=report.verdict,
            approval=ApprovalRead.model_validate(approval),
        )
    return result
