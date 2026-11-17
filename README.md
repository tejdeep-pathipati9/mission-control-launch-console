# Mission Control

Meridian Launch Services anomaly-report console. The backend foundation, classification,
concurrent note checks, briefing review, cost accounting, and human approval are implemented.
The engineer page is implemented in plain HTML/CSS with small JavaScript scripts for submission
and polling. The Flight Director page is the next feature. Both use the agreed white background
and black text. This demo is not certified launch software. Briefing approval never authorizes
a launch or changes a verdict.

## Start

Install and start Docker Desktop, or Docker Engine with the Compose plugin. From the repository root:

```bash
docker compose up --build
```

Open [Mission Control](http://localhost:8000/) to submit an anomaly report and follow its status.
The form accepts readings outside launch limits, shows field errors, and displays the saved verdict,
violations, deadline, findings, and latest briefing. Its report link survives refresh; status polls
every two seconds while processing and stops at a terminal state. If polling fails, **Retry status**
fetches the saved report without submitting it again. **New report** clears the form for another
submission. The director console and its navigation link arrive in feature 8.

[API documentation](http://localhost:8000/docs) remains available.
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
Python calculates token-cost estimates with `Decimal` using the saved pricing snapshot and reported
usage. Missing usage or required rates leave cost unavailable; they are never replaced with zero.
Three separate model checks then run concurrently: prompt injection, missing details, and panic/tone.
Each persists its own `clear`, `flagged`, or `error` result, reason, findings, and token trace as it
finishes. A failed/unavailable check produces `failed` / `required_check_failed`; missing credentials
never yield fabricated clean results. Completed findings survive interrupted processing.
After successful checks, the model drafts a briefing and a separate model call evaluates it.
Failed quality assessments feed the next revision, for at most three drafts/reviews. Each version,
feedback, and call trace is saved. The application renders canonical facts and flagged findings;
changed structured facts force a revision even if the evaluator returns a pass. Narrative quality
still depends on the evaluator. No model output changes the stored verdict or violations.
The first passing draft moves the report to `pending_review`, with no approval record. Three quality
failures produce `quality_review_exhausted`; model-call failures produce `draft_failed` or
`evaluation_failed`. Required-check errors prevent drafting. Neither a model check nor a flagged
finding authorizes launch.
Malformed hidden-instruction replies become visible `flagged` results with `review_required: true`
and an `error_reason`. They are unresolved checks: their traces fail, drafting stops, and flag
acknowledgement cannot make them approvable. Successfully evaluated flags remain reviewable under
the normal human-approval rules.

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

The test command runs 24 JavaScript workflow checks in a build stage with networking disabled,
then the Python tests in a network-disabled container. JavaScript checks use Node's built-in test
runner and an in-memory DOM fixture, with no npm packages. Node is test-only: the runtime app
serves unchanged HTML/CSS/JS files and needs no frontend build step. UI checks can use Docker's
build cache when their files are unchanged.

The test container has no API key and uses temporary databases. It never
mounts the application data volume. Image builds may download dependencies; the test process
does not need internet access. Tests also block external sockets; the real-HTTP integration
test permits loopback only to launch and crash its own local server.

**Verification (November 17, 2026):** 417 Python tests and 24 JavaScript workflow checks passed
locally and through the Docker test command; lint and formatting passed. The credential hook's 11 synthetic tests also passed.
Coverage includes three-round revision/feedback handling, early stopping, canonical-fact rejection,
model failures and missing usage, partial polling, and recovery after a real HTTP process crash
while evaluation is blocked. Completed drafts, check findings, and deterministic verdicts survive
interruption. Model behavior is tested with scripted fakes and mocked SDK HTTP; these tests do
not measure live-model detection or narrative-review accuracy and require no paid model calls.
Accounting tests cover cache reads/writes, decimal rounding, billed failures, incomplete totals,
multiple attempts, and saved prices across restart/configuration changes. The real Anthropic SDK
also completes synthetic full-pipeline tests through a mocked HTTP transport. Approval tests cover
server-side permissions, GO/NO-GO preservation, exact flag acknowledgements, stale versions,
restart persistence, audit-failure rollback, and coordinated concurrent requests with one winner.
UI checks cover validation, duplicate submits, polling/retry, saved links, stale responses, terminal
states, and rendering untrusted model text as text. An isolated production-image Compose smoke
test reached healthy status and served `/`, report-link URLs, CSS/JS, API health, and `/docs`
with HTTP 200. Interactive browser/layout QA remains pending; the DOM fixture is not a visual test.

See [the plan](PLAN.md) for milestones. The start command now serves the engineer page and API;
the full browser workflow still needs the director page from feature 8.

## API

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/` | Engineer form and saved-report status (`?report={id}`) |
| `GET` | `/assets/*` | Local CSS/JavaScript files |
| `POST` | `/api/reports` | Validate, persist the decision, schedule processing; return `202` |
| `GET` | `/api/reports` | Newest-first summaries, `limit` (1–100) and `offset` pagination |
| `GET` | `/api/reports/{id}` | Report, current run, checks, trace, briefing history, approval history |
| `GET` | `/api/health` | Check API and database connectivity |
| `GET` | `/api/auth/me` | Inspect the authenticated local demo identity |
| `POST` | `/api/reports/{id}/approve` | Director approves the current pending briefing with flag acknowledgements |

`current_run` contains that attempt's token/cost totals and `known_cost_usd` subtotal. Report-level
`costs` aggregates all attempts, including failed attempts; `pipeline_runs` retains each attempt's
totals. `total_cost_usd` is null if any recorded step lacks a calculable cost. `known_cost_usd` sums
only available costs and must not be presented as a full total when `cost_complete` is false.
While processing, totals are provisional and `cost_complete` stays false. No-call code/configuration
steps cost zero; simulated model replies have no live price. Older unpriced traces stay unavailable.

Each trace includes timestamps, `latency_ms` (null while running), normalized input/output counts,
and USD cost. `details_json` preserves raw provider usage, the applied rates, component amounts,
rounding policy, and a `cost_status` explaining unavailable amounts. Dollar amounts serialize as
decimal strings, rounded per step to ten decimal places before summing. OpenAI input includes cache
hits; Claude's normalized input sums base, cache reads, and cache writes. Raw counters remain
separate, following [Claude's usage accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Approval uses a small local demo bearer guard, an implementation choice for the assignment's
server-side permission checks. Set a random `DEMO_DIRECTOR_TOKEN` (32–256 ASCII characters,
no whitespace) in your ignored runtime configuration. There is no default director credential.
The optional, distinct `DEMO_ENGINEER_TOKEN` demonstrates an authenticated role without approval
permission. These map to fixed server-owned identities; request fields/headers cannot choose a role
or approver name. `/api/auth/me` is a helper, not an assignment requirement. This demo has no account
registration, password database, or OAuth integration.

Use **Authorize → DemoBearer** in `/docs` with the configured token. Approval requires this body,
using IDs from the latest report detail:

```json
{"run_id": 1, "briefing_version_id": 1, "acknowledged_check_result_ids": []}
```

Include exactly every flagged check's ID; use `[]` only when there are no flags. The server requires
a completed current run, three error-free checks, and its latest quality-passed briefing. Missing or
invalid authentication returns `401`, a non-director gets `403`, an unknown report gets `404`,
state/version/acknowledgement conflicts get `409`, and malformed or forged fields get `422`.
Approval and its audit record commit together; an audit failure rolls back the status change.
The audit stores the authenticated identity, exact version, sorted acknowledged IDs, and UTC time.
Only the report becomes `approved`; completed pipeline history, flags, costs, and launch verdict stay
unchanged. A NO-GO briefing can be approved and still remains NO-GO.

Classification runs through report submission. A `no_go` verdict is a valid stored
report, not a validation failure. Input sanity minima are wind >= 0 knots, temperature >= -273.15°C,
and fuel >= 0%; the API, pure rule function, and engineer form enforce them. No arbitrary upper
bounds are added: wind above 25, temperature below 4 but above absolute zero, and fuel above 100
remain accepted reports with `no_go`. Malformed or non-finite readings, booleans/numeric strings,
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
Set `FOUNDRY_INPUT_USD_PER_MILLION`, `FOUNDRY_CACHED_INPUT_USD_PER_MILLION`, and
`FOUNDRY_OUTPUT_USD_PER_MILLION` together from your deployment's pricing. Nonzero cache writes
also require the corresponding `FOUNDRY_CACHE_WRITE_5M_USD_PER_MILLION` or
`FOUNDRY_CACHE_WRITE_1H_USD_PER_MILLION` rate and a reported TTL breakdown. No write rate is
needed when the provider reports zero writes. Direct Anthropic prices are not assumed for Foundry;
missing prices keep dollar totals unavailable. Rates may be zero but must be finite and nonnegative.
Direct Anthropic access remains optional with `MODEL_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.
Custom direct Anthropic prices use the matching `ANTHROPIC_*_USD_PER_MILLION` variables,
including optional `ANTHROPIC_CACHE_WRITE_5M_USD_PER_MILLION` and
`ANTHROPIC_CACHE_WRITE_1H_USD_PER_MILLION`; custom cache-write prices are never inferred.
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

The shared `.codex/hooks.json` guard blocks common credential reads and withholds recognized
secrets from tool output. Review and trust it through Codex's hook controls before relying on it;
committing the configuration does not activate trust. It is a guardrail, not OS-level isolation.
Run its synthetic tests with `python3 -m unittest discover -s .codex/hooks -p 'test_*.py'`.

Dependencies are locked in `backend/uv.lock`. Lint and formatting checks:

```bash
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
```
