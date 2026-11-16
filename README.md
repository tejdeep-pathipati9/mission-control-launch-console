# Mission Control

Meridian Launch Services anomaly-report console. The backend foundation is implemented;
the frontend and model pipeline are upcoming milestones. This assignment/demo is not
certified launch software. Briefing approval will never authorize a launch or change a verdict.

## Start

Install and start Docker Desktop, or Docker Engine with the Compose plugin. From the repository root:

```bash
docker compose up --build
```

Open [API documentation](http://localhost:8000/docs) to submit a report and poll its result.
Health is available at [GET /api/health](http://localhost:8000/api/health).
No API key is required for this milestone, and no model calls are made.

Valid submissions return `202 Accepted` with a report ID, status URL, and `Location` header.
The report, deterministic verdict, pipeline run, and rule trace are committed together.
The background scaffold then records `failed` / `pipeline_not_implemented`, with an explanation.
It does not generate checks, briefings, or approvals. In-limit reports leave severity/deadline
unknown until classification is implemented; any limit violation immediately sets high severity
and a deadline five minutes after submission.

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

See [validation evidence](docs/VALIDATION.md) for results and
[the plan](PLAN.md) for milestones. The final start command will include the browser UI
once the frontend stack is selected.

## API

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/api/reports` | Validate, persist the decision, schedule processing; return `202` |
| `GET` | `/api/reports` | Newest-first summaries, `limit` (1–100) and `offset` pagination |
| `GET` | `/api/reports/{id}` | Report, current run, checks, trace, briefing history, approval history |
| `GET` | `/api/health` | Check API and database connectivity |

Approval and model endpoints are not implemented yet. A `no_go` verdict is a valid stored
report, not a validation failure. Malformed or non-finite readings, booleans/numeric strings,
missing/blank required text, and unexpected fields receive clear `422` responses.

Example submission from another terminal:

```bash
curl -i http://localhost:8000/api/reports \
  -H 'Content-Type: application/json' \
  -d '{"engineer_name":"Avery Chen","station":"Weather","wind_knots":25,"temp_c":4,"fuel_pct":98,"note":"Monitoring gusts; backup sensor agrees."}'
```

## Backend development without Docker

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
