from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.classification import Classification

Text = Annotated[str, Field(min_length=1, max_length=1600)]


class BriefingFacts(Classification):
    verdict: Literal["go", "no_go"]
    violations: list[Text]
    brief_by_at: Text


class BriefingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)

    summary: Text
    facts: BriefingFacts


class BriefingEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)

    quality_passed: bool
    feedback: Annotated[list[Text], Field(max_length=10)]

    @model_validator(mode="after")
    def rejection_needs_feedback(self):
        if not self.quality_passed and not self.feedback:
            raise ValueError("A rejected draft requires actionable feedback")
        return self
