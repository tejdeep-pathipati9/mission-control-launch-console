from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.launch_rules import Severity


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    subsystem: Literal["propulsion", "avionics", "weather", "ground_systems", "other", "unknown"]
    severity: Severity
