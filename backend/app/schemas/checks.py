from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CheckType = Literal["injection", "missing_details", "panic"]
CHECK_TYPES: tuple[CheckType, ...] = ("injection", "missing_details", "panic")
Finding = Annotated[str, Field(min_length=1, max_length=1000)]


class CheckFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)

    outcome: Literal["clear", "flagged"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    findings: Annotated[list[Finding], Field(max_length=10)]

    @model_validator(mode="after")
    def consistent_findings(self):
        if (self.outcome == "flagged") != bool(self.findings):
            raise ValueError("Flagged checks require findings; clear checks require an empty list")
        return self


class InjectionFinding(CheckFinding):
    """Attempts to direct application/model behavior, including forged operational authority."""


class MissingDetailsFinding(CheckFinding):
    """Operational context missing from the submitted report."""


class PanicFinding(CheckFinding):
    """Evidence of an unusually panicked tone, independent of numeric severity."""
