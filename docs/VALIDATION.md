# Foundation validation

Verified November 16, 2026 on `feature/backend-foundation`. Scope: backend foundation,
deterministic rules, container startup, and persistence. Model processing, approval, and
the browser console are not implemented yet.

## Automated checks

From the repository root:

```text
backend/.venv/bin/ruff check backend
All checks passed!

backend/.venv/bin/ruff format --check backend
20 files already formatted

UV_CACHE_DIR=/tmp/mission-control-uv-cache uv run --directory backend --offline pytest -q
74 passed in 1.43s

docker compose --profile test run --build --rm --no-deps tests
74 passed in 2.38s
```

The Docker test service uses `network_mode: none`, has no app-volume mount, and an empty
API key. Tests remove `OPENAI_API_KEY` and block external sockets. The real-HTTP test
only permits loopback. Build-time package downloads are separate from offline test execution.

Coverage includes all required input fields, whitespace/missing/malformed values,
booleans/numeric strings, non-finite readings, exact and just-outside launch thresholds,
all-violation reporting, severity/deadline mapping, foreign keys/check uniqueness,
transaction rollback on commit failure, scheduling/background failures, and isolated databases.

`test_real_http_returns_202_while_pipeline_is_blocked_and_recovers_after_crash` starts
a real Uvicorn subprocess with a coordinated blocked pipeline. It verifies `202` and a
pollable `processing` state while the pipeline remains blocked, kills the process, then
starts the normal application against the same file. It verifies `failed` / `interrupted`,
the unchanged verdict, preserved flagged finding and completed trace, and an interrupted
unfinished trace whose unknown usage remains unknown.

## Container checks

Validated the documented start command in detached/wait form:

```text
docker compose config --quiet
(exit 0)

docker compose up --build --detach --wait
Container mission-control-launch-console-app-1 Healthy
```

Submitted two clearly labeled container-verification reports through the HTTP API:

```json
{"report_id": 1, "wind_knots": 25, "verdict": "go", "status": "failed"}
{"report_id": 2, "wind_knots": 26, "verdict": "no_go", "status": "failed"}
```

Both submissions returned `202`; subsequent polls showed `pipeline_not_implemented`,
as expected for the scaffold. They used `temp_c=4` and `fuel_pct=98`.
Seeded a third clearly labeled unfinished run and trace in the container's database,
then replaced the container without removing its named volume:

```text
docker compose up --detach --force-recreate --wait app
Container mission-control-launch-console-app-1 Healthy
```

Verified by HTTP inside the replacement container:

```json
{"saved_reports_after_recreation": 3, "go_report_preserved": true, "no_go_report_preserved": true, "interrupted_run_recovered": true, "completed_rule_trace_preserved": true, "api_docs_status": 200}
```

These three verification records remain in the local Docker volume, separate from all
automated-test databases. The image runs as non-root and can write the named data volume.
