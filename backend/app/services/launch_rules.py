from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Literal

Severity = Literal["high", "medium", "low"]
MIN_TEMPERATURE_C = -273.15


@dataclass(frozen=True)
class LaunchDecision:
    verdict: Literal["go", "no_go"]
    violations: tuple[str, ...]


def evaluate_launch_limits(wind_knots: float, temp_c: float, fuel_pct: float) -> LaunchDecision:
    if not all(isfinite(value) for value in (wind_knots, temp_c, fuel_pct)):
        raise ValueError("Launch readings must be finite")
    if wind_knots < 0:
        raise ValueError("Wind speed must be nonnegative")
    if temp_c < MIN_TEMPERATURE_C:
        raise ValueError("Temperature cannot be below absolute zero (-273.15°C)")
    if fuel_pct < 0:
        raise ValueError("Fuel percentage must be nonnegative")
    violations = []
    if wind_knots > 25:
        violations.append("wind_knots must be <= 25")
    if temp_c < 4:
        violations.append("temp_c must be >= 4")
    if not 98 <= fuel_pct <= 100:
        violations.append("fuel_pct must be between 98 and 100 inclusive")
    return LaunchDecision("no_go" if violations else "go", tuple(violations))


def resolve_severity(classified: Severity, violations: tuple[str, ...]) -> Severity:
    if classified not in {"high", "medium", "low"}:
        raise ValueError("Unknown severity")
    return "high" if violations else classified


def calculate_brief_by(created_at: datetime, severity: Severity) -> datetime:
    if created_at.tzinfo is None:
        raise ValueError("Report creation time must be timezone-aware")
    minutes = {"high": 5, "medium": 15, "low": 30}
    if severity not in minutes:
        raise ValueError("Unknown severity")
    return created_at + timedelta(minutes=minutes[severity])
