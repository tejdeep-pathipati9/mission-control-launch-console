import os
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str | None = None
    model_timeout_seconds: float = 30
    model_max_output_tokens: int = 1024

    def __post_init__(self):
        if not isfinite(self.model_timeout_seconds) or self.model_timeout_seconds <= 0:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be positive and finite")
        if type(self.model_max_output_tokens) is not int or self.model_max_output_tokens <= 0:
            raise ValueError("MODEL_MAX_OUTPUT_TOKENS must be a positive integer")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_path=Path(
                os.environ.get("MISSION_CONTROL_DB_PATH", "data/mission_control.db")
            ),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip() or None,
            openai_model=os.environ.get("OPENAI_MODEL", "").strip() or None,
            model_timeout_seconds=float(os.environ.get("MODEL_TIMEOUT_SECONDS", "30")),
            model_max_output_tokens=int(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "1024")),
        )
