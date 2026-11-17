from app.db.models import CheckResult


def check_completed(result: CheckResult) -> bool:
    """A fail-safe review flag is still unresolved when evaluation recorded an error."""
    return (
        result.outcome in {"clear", "flagged"}
        and (result.details_json or {}).get("error_reason") is None
    )
