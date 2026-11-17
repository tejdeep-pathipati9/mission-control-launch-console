import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path

DEFAULT_MODEL = "gpt-5.4-mini-2026-03-17"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"


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
    model_provider: str = "openai"
    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_model: str | None = DEFAULT_ANTHROPIC_MODEL
    anthropic_input_usd_per_million: Decimal | None = None
    anthropic_cached_input_usd_per_million: Decimal | None = None
    anthropic_output_usd_per_million: Decimal | None = None
    anthropic_foundry_api_key: str | None = field(default=None, repr=False)
    anthropic_foundry_resource: str | None = None
    anthropic_foundry_base_url: str | None = None
    foundry_input_usd_per_million: Decimal | None = None
    foundry_cached_input_usd_per_million: Decimal | None = None
    foundry_output_usd_per_million: Decimal | None = None

    def __post_init__(self):
        if not isfinite(self.model_timeout_seconds) or self.model_timeout_seconds <= 0:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be positive and finite")
        if type(self.model_max_output_tokens) is not int or self.model_max_output_tokens <= 0:
            raise ValueError("MODEL_MAX_OUTPUT_TOKENS must be a positive integer")
        if self.model_provider not in {"openai", "anthropic", "foundry"}:
            raise ValueError("MODEL_PROVIDER must be openai, anthropic, or foundry")
        for provider in ("openai", "anthropic", "foundry"):
            rates = tuple(
                getattr(self, f"{provider}_{kind}_usd_per_million")
                for kind in ("input", "cached_input", "output")
            )
            if any(rate is not None for rate in rates):
                if any(rate is None for rate in rates):
                    raise ValueError(
                        f"Configure all three {provider.upper()}_*_USD_PER_MILLION rates together"
                    )
                if any(not rate.is_finite() or rate < 0 for rate in rates):
                    raise ValueError("Model prices must be finite and nonnegative")

    @property
    def selected_model(self) -> str | None:
        return self.openai_model if self.model_provider == "openai" else self.anthropic_model

    def pricing_for(self, model: str, provider: str | None = None) -> dict[str, str] | None:
        provider = provider or ("anthropic" if model == DEFAULT_ANTHROPIC_MODEL else "openai")
        if provider not in {"openai", "anthropic", "foundry"}:
            return None
        configured_model = (
            self.anthropic_model if provider == "foundry" else getattr(self, f"{provider}_model")
        )
        input_rate = getattr(self, f"{provider}_input_usd_per_million")
        if (model == configured_model or provider == "foundry") and input_rate is not None:
            return {
                "model": model,
                "input_usd_per_million": str(input_rate),
                "cached_input_usd_per_million": str(
                    getattr(self, f"{provider}_cached_input_usd_per_million")
                ),
                "output_usd_per_million": str(getattr(self, f"{provider}_output_usd_per_million")),
                "source": "configuration",
            }
        if provider == "foundry":
            return None  # Deployment/region pricing must be explicitly configured for Foundry.
        if provider == "anthropic" and model == DEFAULT_ANTHROPIC_MODEL:
            return {
                "model": model,
                "input_usd_per_million": "5.00",
                "cached_input_usd_per_million": "0.50",
                "output_usd_per_million": "25.00",
                "cache_write_5m_usd_per_million": "6.25",
                "cache_write_1h_usd_per_million": "10.00",
                "source": "https://platform.claude.com/docs/en/models/opus-5/overview",
                "verified_on": "2026-11-17",
            }
        if provider == "openai" and model == DEFAULT_MODEL:
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
            model_provider=os.environ.get("MODEL_PROVIDER", "foundry").strip().lower(),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip() or None,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip()
            or None,
            anthropic_foundry_api_key=os.environ.get("ANTHROPIC_FOUNDRY_API_KEY", "").strip()
            or None,
            anthropic_foundry_resource=os.environ.get("ANTHROPIC_FOUNDRY_RESOURCE", "").strip()
            or None,
            anthropic_foundry_base_url=os.environ.get("ANTHROPIC_FOUNDRY_BASE_URL", "").strip()
            or None,
            **{
                name.lower(): _rate_from_environment(name)
                for name in (
                    "OPENAI_INPUT_USD_PER_MILLION",
                    "OPENAI_CACHED_INPUT_USD_PER_MILLION",
                    "OPENAI_OUTPUT_USD_PER_MILLION",
                    "ANTHROPIC_INPUT_USD_PER_MILLION",
                    "ANTHROPIC_CACHED_INPUT_USD_PER_MILLION",
                    "ANTHROPIC_OUTPUT_USD_PER_MILLION",
                    "FOUNDRY_INPUT_USD_PER_MILLION",
                    "FOUNDRY_CACHED_INPUT_USD_PER_MILLION",
                    "FOUNDRY_OUTPUT_USD_PER_MILLION",
                )
            },
        )
