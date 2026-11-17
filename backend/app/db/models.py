from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """SQLite stores naive UTC; application/API values are always timezone-aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("A timezone-aware timestamp is required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("verdict IN ('go', 'no_go')", name="report_verdict"),
        CheckConstraint(
            "status IN ('processing', 'pending_review', 'approved', 'failed')",
            name="report_status",
        ),
        CheckConstraint("severity IN ('high', 'medium', 'low')", name="report_severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    engineer_name: Mapped[str] = mapped_column(String(120))
    station: Mapped[str] = mapped_column(String(120))
    wind_knots: Mapped[float]
    temp_c: Mapped[float]
    fuel_pct: Mapped[float]
    note: Mapped[str] = mapped_column(Text)
    subsystem: Mapped[str | None] = mapped_column(String(40))
    severity: Mapped[str | None] = mapped_column(String(10))
    verdict: Mapped[str] = mapped_column(String(10))
    violations_json: Mapped[list[str]] = mapped_column(JSON)
    brief_by_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(20), default="processing")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'pending_review', 'failed')", name="run_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    failure_reason: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(120))
    total_input_tokens: Mapped[int | None] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int | None] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), default=Decimal(0))
    cost_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (
        UniqueConstraint("run_id", "check_type"),
        CheckConstraint(
            "check_type IN ('injection', 'missing_details', 'panic')", name="check_type"
        ),
        CheckConstraint("outcome IN ('clear', 'flagged', 'error')", name="check_outcome"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    check_type: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class TraceStep(Base):
    __tablename__ = "trace_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'completed', 'failed', 'interrupted')", name="step_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    step_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BriefingVersion(Base):
    __tablename__ = "briefing_versions"
    __table_args__ = (
        UniqueConstraint("run_id", "round_number"),
        CheckConstraint("round_number BETWEEN 1 AND 3", name="briefing_round"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    round_number: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    evaluator_feedback: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quality_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    briefing_version_id: Mapped[int] = mapped_column(
        ForeignKey("briefing_versions.id"), unique=True
    )
    approved_by: Mapped[str] = mapped_column(String(120))
    acknowledged_flags_json: Mapped[list[int]] = mapped_column(JSON, default=list)
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
