from datetime import UTC, datetime, timedelta

import pytest

from app.services.launch_rules import (
    calculate_brief_by,
    evaluate_launch_limits,
    resolve_severity,
)


@pytest.mark.parametrize(
    ("wind", "temp", "fuel", "verdict"),
    [
        (25, 4, 98, "go"),
        (25, 4, 100, "go"),
        (25.001, 4, 99, "no_go"),
        (25, 3.999, 99, "no_go"),
        (25, 4, 97.999, "no_go"),
        (25, 4, 100.001, "no_go"),
        (24.999, 4.001, 98.001, "go"),
        (0, 50, 99.999, "go"),
        (-1, 4, 98, "go"),
        (0, -20, -1, "no_go"),
    ],
)
def test_launch_limit_boundaries(wind, temp, fuel, verdict):
    decision = evaluate_launch_limits(wind, temp, fuel)
    assert decision.verdict == verdict
    assert bool(decision.violations) == (verdict == "no_go")


def test_all_violations_are_reported():
    decision = evaluate_launch_limits(26, 3, 101)
    assert len(decision.violations) == 3
    assert [violation.split()[0] for violation in decision.violations] == [
        "wind_knots",
        "temp_c",
        "fuel_pct",
    ]


@pytest.mark.parametrize("severity,minutes", [("high", 5), ("medium", 15), ("low", 30)])
def test_severity_deadline_mapping_and_override(severity, minutes):
    created = datetime(2026, 11, 16, 23, 58, tzinfo=UTC)
    assert resolve_severity(severity, ()) == severity
    assert calculate_brief_by(created, severity) == created + timedelta(minutes=minutes)
    effective = resolve_severity(severity, ("wind_knots must be <= 25",))
    assert effective == "high"
    assert calculate_brief_by(created, effective) == created + timedelta(minutes=5)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rule_function_also_rejects_nonfinite_values(value):
    with pytest.raises(ValueError, match="finite"):
        evaluate_launch_limits(value, 4, 98)


def test_invalid_severity_and_naive_timestamp_rejected():
    with pytest.raises(ValueError, match="severity"):
        resolve_severity("urgent", ())
    with pytest.raises(ValueError, match="severity"):
        calculate_brief_by(datetime.now(UTC), "urgent")
    with pytest.raises(ValueError, match="timezone"):
        calculate_brief_by(datetime(2026, 11, 16), "high")
