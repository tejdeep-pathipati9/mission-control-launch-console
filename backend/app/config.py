import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path

DEFAULT_MODEL = "gpt-5.4-mini-2026-03-17"


def _rate_from_environment(name: str) -> Decimal | None:
    value = os.environ.get(name, "").strip()
    try:
        return Decimal(value) if value else None
    except InvalidOperation:
        raise ValueError(f"{name} must be a finite nonnegative number") from None


@dataclass(frozen=True)
class Settings:
    database_path: Path
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str | None = None
    model_timeout_seconds: float = 30
    model_max_output_tokens: int = 1024
    openai_input_usd_per_million: Decimal | None = None
    openai_cached_input_usd_per_million: Decimal | None = None
    openai_output_usd_per_million: Decimal | None = None

    def __post_init__(self):
        if not isfinite(self.model_timeout_seconds) or self.model_timeout_seconds <= 0:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be positive and finite")
        if type(self.model_max_output_tokens) is not int or self.model_max_output_tokens <= 0:
            raise ValueError("MODEL_MAX_OUTPUT_TOKENS must be a positive integer")
        rates = (
            self.openai_input_usd_per_million,
            self.openai_cached_input_usd_per_million,
            self.openai_output_usd_per_million,
        )
        if any(rate is not None for rate in rates):
            if any(rate is None for rate in rates):
                raise ValueError("Configure all three OPENAI_*_USD_PER_MILLION rates together")
            if any(not rate.is_finite() or rate < 0 for rate in rates):
                raise ValueError("Model prices must be finite and nonnegative")

    def pricing_for(self, model: str) -> dict[str, str] | None:
        if model == self.openai_model and self.openai_input_usd_per_million is not None:
            return {
                "model": model,
                "input_usd_per_million": str(self.openai_input_usd_per_million),
                "cached_input_usd_per_million": str(self.openai_cached_input_usd_per_million),
                "output_usd_per_million": str(self.openai_output_usd_per_million),
                "source": "configuration",
            }
        if model == DEFAULT_MODEL:
            return {
                "model": model,
                "input_usd_per_million": "0.75",
                "cached_input_usd_per_million": "0.075",
                "output_usd_per_million": "4.50",
                "source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
                "verified_on": "2026-11-16",
            }
        return None

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_path=Path(
                os.environ.get("MISSION_CONTROL_DB_PATH", "data/mission_control.db")
            ),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip() or None,
            openai_model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or None,
            model_timeout_seconds=float(os.environ.get("MODEL_TIMEOUT_SECONDS", "30")),
            model_max_output_tokens=int(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "1024")),
            **{
                name.lower(): _rate_from_environment(name)
                for name in (
                    "OPENAI_INPUT_USD_PER_MILLION",
                    "OPENAI_CACHED_INPUT_USD_PER_MILLION",
                    "OPENAI_OUTPUT_USD_PER_MILLION",
                )
            },
        )
