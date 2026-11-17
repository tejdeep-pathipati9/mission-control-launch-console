from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.services.launch_rules import MIN_TEMPERATURE_C

Severity = Literal["high", "medium", "low"]
Status = Literal["processing", "pending_review", "approved", "failed"]
Reading = Annotated[float, Field(strict=True, allow_inf_nan=False)]
RequiredText = Annotated[str, Field(min_length=1, max_length=120)]


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    engineer_name: RequiredText
    station: RequiredText
    wind_knots: Annotated[Reading, Field(ge=0)]
    temp_c: Annotated[Reading, Field(ge=MIN_TEMPERATURE_C)]
    fuel_pct: Annotated[Reading, Field(ge=0)]
    note: Annotated[str, Field(min_length=1, max_length=10000)]


class ReportAccepted(BaseModel):
    report_id: int
    status: Literal["processing"] = "processing"
    status_url: str


class ORMRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CheckRead(ORMRead):
    id: int
    run_id: int
    check_type: Literal["injection", "missing_details", "panic"]
    outcome: Literal["clear", "flagged", "error"]
    reason: str
    details_json: dict[str, Any] | None
    created_at: datetime


class TraceRead(ORMRead):
    id: int
    run_id: int
    step_name: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    details_json: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None

    @computed_field
    @property
    def latency_ms(self) -> int | None:
        if self.completed_at is None:
            return None
        return max(0, round((self.completed_at - self.started_at).total_seconds() * 1000))


class BriefingRead(ORMRead):
    id: int
    report_id: int
    run_id: int
    round_number: int
    content: str
    evaluator_feedback: dict[str, Any] | None
    quality_passed: bool
    created_at: datetime


class ApprovalRead(ORMRead):
    id: int
    report_id: int
    briefing_version_id: int
    approved_by: str
    acknowledged_flags_json: list[int]
    approved_at: datetime


class RunRead(ORMRead):
    id: int
    report_id: int
    status: str
    failure_reason: str | None
    failure_message: str | None
    model_name: str | None
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_cost_usd: Decimal | None
    known_cost_usd: Decimal
    cost_complete: bool
    started_at: datetime
    completed_at: datetime | None


class ReportCosts(BaseModel):
    """Totals across all processing attempts; known cost is a subtotal, not a full bill."""

    total_input_tokens: int | None
    total_output_tokens: int | None
    total_cost_usd: Decimal | None
    known_cost_usd: Decimal
    cost_complete: bool


class ReportSummary(ORMRead):
    id: int
    engineer_name: str
    station: str
    subsystem: str | None
    severity: Severity | None
    verdict: Literal["go", "no_go"]
    violations_json: list[str]
    brief_by_at: datetime | None
    status: Status
    created_at: datetime
    updated_at: datetime
    current_run: RunRead
    costs: ReportCosts
    flags: list[CheckRead]


class ReportDetail(ReportSummary):
    wind_knots: float
    temp_c: float
    fuel_pct: float
    note: str
    checks: list[CheckRead]
    trace: list[TraceRead]
    briefing_versions: list[BriefingRead]
    latest_briefing: BriefingRead | None
    approvals: list[ApprovalRead]
    pipeline_runs: list[RunRead]
