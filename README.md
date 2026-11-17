# Mission Control

Meridian Launch Services anomaly-report console. The backend foundation, classification, and
concurrent note checks are implemented; briefing review and the frontend are upcoming milestones. This demo is not
certified launch software. Briefing approval will never authorize a launch or change a verdict.

## Start

Install and start Docker Desktop, or Docker Engine with the Compose plugin. From the repository root:

```bash
docker compose up --build
```

Open [API documentation](http://localhost:8000/docs) to submit a report and poll its result.
Health is available at [GET /api/health](http://localhost:8000/api/health).
For live processing, copy `.env.example` to an ignored `.env` and set `ANTHROPIC_FOUNDRY_API_KEY`
before starting Compose. Without a key, reports explicitly fall back to `unknown` / `high`;
the API remains usable and no model calls are made. Tests always use injected fakes or mocked HTTP.

Valid submissions return `202 Accepted` with a report ID, status URL, and `Location` header.
The report, deterministic verdict, pipeline run, and rule trace are committed together.
Background classification validates a structured subsystem/severity response, falling back to
`unknown` / `high` on invalid output, refusal, timeout, or call failure. Any launch-limit breach
forces high severity; deadlines use the original submission time. Trace entries retain the
classification, fallback reason, model identity, reported usage, and applicable pricing snapshot.
Cost for attempted model calls remains unavailable until the accounting feature calculates it;
missing usage is never zeroed.
Three separate model checks then run concurrently: prompt injection, missing details, and panic/tone.
Each persists its own `clear`, `flagged`, or `error` result, reason, findings, and token trace as it
finishes. A failed/unavailable check produces `failed` / `required_check_failed`; missing credentials
never yield fabricated clean results. Completed findings survive interrupted processing.
After successful checks, the run still stops at `failed` / `pipeline_not_implemented` because briefing
generation/review is a later feature. Neither a model check nor a flagged finding authorizes launch.

The application uses one worker and a named volume for `/data/mission_control.db`.
Stop it with `docker compose down`; records survive that command and container recreation.
On startup, unfinished processing runs become `failed` / `interrupted` and retain their history.
Do not use `docker compose down --volumes` unless you intend to delete the database.
Keep one app container/worker while this recovery strategy is in use.

## Test

From the repository root:

```bash
docker compose --profile test run --build --rm --no-deps tests
```

The test container has networking disabled, no API key, and temporary databases. It never
mounts the application data volume. Image builds may download dependencies; the test process
does not need internet access. Tests also block external sockets; the real-HTTP integration
test permits loopback only to launch and crash its own local server.

**Verification (November 17, 2026):** 224 tests passed locally and in network-disabled Docker
(`224 passed in 12.01s` in Docker); lint and formatting passed. Tests cover independent errors/timeouts, concurrent
execution using coordinated calls, partial polling results, and process-crash recovery. Starting with
`docker compose up --build --detach --wait` reached healthy status. Container recreation
preserved saved reports and marked unfinished processing as `failed` / `interrupted`
without changing verdicts or completed traces. Model behavior is verified through scripted fakes
and mocked SDK HTTP. Injection fixtures test result handling and verdict protection; they do not
measure live-model detection accuracy. No paid live-model calls are required by the tests.

See [the plan](PLAN.md) for milestones. The final start command will include the browser UI
once the frontend stack is selected.

## API

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/api/reports` | Validate, persist the decision, schedule processing; return `202` |
| `GET` | `/api/reports` | Newest-first summaries, `limit` (1–100) and `offset` pagination |
| `GET` | `/api/reports/{id}` | Report, current run, checks, trace, briefing history, approval history |
| `GET` | `/api/health` | Check API and database connectivity |

Approval is not implemented yet; classification runs through report submission. A `no_go` verdict is a valid stored
report, not a validation failure. Malformed or non-finite readings, booleans/numeric strings,
missing/blank required text, and unexpected fields receive clear `422` responses.

Example submission from another terminal:

```bash
curl -i http://localhost:8000/api/reports \
  -H 'Content-Type: application/json' \
  -d '{"engineer_name":"Avery Chen","station":"Weather","wind_knots":25,"temp_c":4,"fuel_pct":98,"note":"Monitoring gusts; backup sensor agrees."}'
```

## Backend development without Docker

The selected provider is Microsoft Foundry (`MODEL_PROVIDER=foundry`) for Claude Opus 5.
Set `ANTHROPIC_FOUNDRY_API_KEY` and either `ANTHROPIC_FOUNDRY_RESOURCE` or
`ANTHROPIC_FOUNDRY_BASE_URL` (ending in `/anthropic/`). `ANTHROPIC_MODEL` is your Foundry
deployment name; the template uses `claude-opus-5`, which must match the name you deployed.
These are the [SDK's Foundry connection settings](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry).
Set all three `FOUNDRY_*_USD_PER_MILLION` values from your deployment's pricing to record rates;
direct Anthropic list prices are not assumed for Foundry. Cache usage remains separately identified.
Direct Anthropic access remains optional with `MODEL_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.
The existing OpenAI adapter remains available with `MODEL_PROVIDER=openai`, `OPENAI_API_KEY`,
and `OPENAI_MODEL`; providers never switch automatically after failures.
`MODEL_TIMEOUT_SECONDS` defaults to 30 and `MODEL_MAX_OUTPUT_TOKENS` to 1024.
The live adapter rejects missing configuration; the pipeline records that as a safe fallback
without automatically selecting a fake.

With uv and Python 3.12–3.14 installed, run these from the repository root:

```bash
uv run --directory backend uvicorn app.main:app --reload
uv run --directory backend pytest -q
```

Local data defaults to `backend/data/mission_control.db`. Set `MISSION_CONTROL_DB_PATH`
in the environment to change it; see [.env.example](.env.example). The app does not
automatically load `.env` files. Never point tests at your application database.

Dependencies are locked in `backend/uv.lock`. Lint and formatting checks:

```bash
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
```
