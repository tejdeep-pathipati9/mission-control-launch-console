from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.reports import ApprovalRead

Identifier = Annotated[int, Field(strict=True, gt=0)]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: Identifier
    briefing_version_id: Identifier
    acknowledged_check_result_ids: Annotated[list[Identifier], Field(max_length=3)]

    @field_validator("acknowledged_check_result_ids")
    @classmethod
    def unique_acknowledgements(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Acknowledgement IDs must be unique")
        return values


class ApprovalResponse(BaseModel):
    report_id: int
    run_id: int
    status: Literal["approved"] = "approved"
    verdict: Literal["go", "no_go"]
    approval: ApprovalRead
