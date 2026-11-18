# Mission Control

An anomaly-report console for Meridian Launch Services. Engineers submit reports; the Flight
Director reviews a short model-written briefing, launch verdict, checks, trace, and calculated cost.
Built with FastAPI, SQLite, plain HTML/CSS/JavaScript, and Claude Opus 5 through Microsoft Foundry.
See [tool choices](TOOL_CHOICES.md) for the reasons and tradeoffs.

**Approving a briefing never authorizes launch or changes its GO/NO-GO verdict.**

## Run

Install and start Docker Desktop, or Docker Engine with the Compose plugin. For live model
processing, copy [.env.example](.env.example) to an ignored `.env` in the repository root and set:

| Setting | Value |
| --- | --- |
| `ANTHROPIC_FOUNDRY_API_KEY` | Your Foundry API key; used only by the backend. |
| `ANTHROPIC_FOUNDRY_RESOURCE` **or** `ANTHROPIC_FOUNDRY_BASE_URL` | Set exactly one. The base URL must end in `/anthropic/`. |
| `ANTHROPIC_MODEL` | Your deployment name; the template uses `claude-opus-5`. |
| `DEMO_DIRECTOR_TOKEN` | A random 32–256 character ASCII access key without whitespace, for human approval. Keep it separate from the model API key. |
| `FOUNDRY_*_USD_PER_MILLION` | The template supplies Opus 5 Global Standard list prices. Adjust for your deployment; see [pricing](#pricing-and-persistence). |

From the repository root, start the complete app:

```bash
docker compose up --build
```

Open [New report](http://localhost:8000/), [Reports](http://localhost:8000/reports), or the
[API documentation](http://localhost:8000/docs). The same container serves the frontend and API;
no separate frontend install or build is needed.

Without model configuration, the app still starts and saves reports with code-calculated verdicts.
Classification falls back to `unknown` / `high`, required checks fail, and approval is blocked.
The live application never substitutes a fake model. Never commit runtime credentials.

## Test

From the repository root, run the complete test suite:

```bash
docker compose --profile test run --build --rm --no-deps tests
```

No API key or model configuration is needed. JavaScript checks run with networking disabled during
image construction; Python tests run in a network-disabled container with temporary databases and
no application-volume mount. Model calls use the shared interface with fakes or mocked SDK HTTP.
The HTTP restart tests use loopback only. Initial image builds may download dependencies; tests
make no external network calls. An unchanged JavaScript test stage can be cached by Docker.

Verification on **November 18, 2026**:

```text
Python: 436 passed
JavaScript: 58 passed, 0 failed
Lint and formatting: passed
Credential guard: 11 passed
```

The complete app also passed a container startup, page/API smoke check, and restart check.
All 10 local saved reports, including the live costs and approval audits below, survived unchanged.

## Use the console

1. Submit an engineer name, station, wind, temperature, fuel percentage, and anomaly note.
   Field errors explain invalid input. Valid submissions open a separate `/reports/{id}` page.
2. Review the model briefing, verdict, deadline, readings, and the single table of all three checks.
   Expand findings, original input, review notes, or individual trace cards when needed.
3. For a pending briefing, expand **Flight Director access**, enter `DEMO_DIRECTOR_TOKEN`, and
   choose **Sign in**. Review the full saved briefing and acknowledge every flagged check before
   choosing **Approve briefing**. Processing, failed, and unresolved reports cannot be approved.
4. Return to **Reports** to open another report. Each report has its own URL; selecting one never
   expands details below the queue. The queue refreshes every five seconds; processing reports
   poll every two seconds. **Retry status** only fetches saved data and does not resubmit it.

Approval access is a small server-checked demo role, with no account-registration system. There is
no default Director credential. An optional, distinct `DEMO_ENGINEER_TOKEN` demonstrates a role
that cannot approve. Browser credentials stay in page memory and clear on navigation or sign-out.

## Pipeline and rules

1. **Validate and save.** The backend commits the report, numeric verdict, violations, run, and
   launch-limit trace before returning `202 Accepted`. Background processing uses fresh sessions.
   All fields are required; malformed/non-finite readings, numeric strings, booleans, and blank
   text are rejected. Negative wind/fuel and temperatures below absolute zero are also rejected.
2. **Check launch limits in Python.** Wind must be **≤25 knots**, temperature **≥4°C**, and fuel
   **98–100% inclusive**. A breach is a valid report with `no_go`, not a form-validation error.
   The model cannot change readings, violations, or the verdict.
3. **Classify with the model.** A validated schema restricts subsystem and severity. Invalid output,
   refusal, timeout, or unavailable classification falls back to `unknown` / `high`. Every limit
   breach forces high severity. Deadlines are measured from report creation: **high → 5 minutes**,
   **medium → 15 minutes**, **low → 30 minutes**.
4. **Run three checks concurrently.** Separate model calls check hidden instructions, missing
   details, and panic/tone. Report text is untrusted data. Completed flags remain visible for human
   review. An unavailable check fails processing; malformed hidden-instruction output flags the
   note as unresolved and blocks drafting/approval.
5. **Draft and review.** The model writes a short briefing; a separate model call evaluates it.
   Rejected feedback goes into the next draft. The loop stops on the first pass or after three
   rounds. Summaries target 20–45 words, with an 80-word maximum; Python also rejects repeated
   sentences, internal jargon, full reading recitals, and changed canonical facts.
6. **Wait for a person.** Successful checks and quality review produce `pending_review`, with no
   approval record. An authenticated Director action approves the exact current version and
   records identity, timestamp, and flag acknowledgements atomically. Stale/duplicate requests
   are rejected. A NO-GO briefing stays NO-GO after approval.

The writer and reviewer use **separate calls to the same configured model**, with distinct
instructions and schemas. Neither role can approve. The assignment does not require two different
models or an agent framework. Three failed reviews, a required-check failure, or an interrupted
run produces `failed`, which cannot be approved.

Historical briefings retain their original text and costs. Long or jargon-heavy older versions
show a clearly labelled application-generated **Report overview**, with the exact model draft
under **Full saved briefing**. Fresh concise model summaries are displayed directly.

## Rubric evidence

Each criterion has implementation and verification evidence in the repository:

| Criterion | Points | Implementation | Verification |
| --- | ---: | --- | --- |
| One command to start; one to test | 10 | [Run](#run), [Test](#test), [Compose](compose.yaml), [Dockerfile](Dockerfile) | Command results above; startup and restart checks. |
| Tool choices and tradeoffs | 10 | [TOOL_CHOICES.md](TOOL_CHOICES.md) | Backend, frontend, database, and model each have a specific reason, rejected alternative, and customer concern. |
| Form, validation, persistence | 10 | [Submission](backend/app/static/assets/submission.js), [request schema](backend/app/schemas/reports.py) | [API validation](backend/tests/test_reports_api.py), [restart/crash recovery](backend/tests/test_persistence.py), [form workflow](backend/tests/frontend/submission.test.cjs). |
| Code-based verdict and exact limits | 15 | [Launch rules](backend/app/services/launch_rules.py) | [Boundary tests](backend/tests/test_launch_rules.py): wind 25/25.001, temperature 4/3.999, fuel 98/97.999 and 100/100.001. |
| Severity and brief-by time | 10 | [Deadline rules](backend/app/services/launch_rules.py), [effective classification](backend/app/services/classification.py) | [5/15/30-minute and high-override tests](backend/tests/test_launch_rules.py), [saved deadlines](backend/tests/test_classification.py). |
| Structured model classification and fallback | 10 | [Schema](backend/app/schemas/classification.py), [classifier](backend/app/services/classification.py) | [Invalid output, refusal, timeout, and safe fallback](backend/tests/test_classification.py). |
| Concurrent checks and hidden instructions | 10 | [Safety checks](backend/app/services/safety_checks.py) | [Concurrency barrier, instruction-bearing notes, visible flags, unchanged NO-GO, malformed replies](backend/tests/test_safety_checks.py). |
| Draft, review, revision cap, human approval | 10 | [Briefing loop](backend/app/services/briefings.py), [approval service](backend/app/services/approvals.py) | [Feedback and three-round cap](backend/tests/test_briefings.py), [authorization, pending-only approval, audit, and verdict preservation](backend/tests/test_approvals.py). |
| One trace card per step, tokens, calculated cost | 10 | [Trace cards](backend/app/static/assets/report.js), [Decimal cost calculation](backend/app/services/costs.py) | [Cost math](backend/tests/test_costs.py), [pipeline totals and saved rates](backend/tests/test_cost_pipeline.py), [rendered trace cards](backend/tests/frontend/review.test.cjs). |
| Offline tests with a replaceable model | 5 | [ModelClient](backend/app/llm/client.py), [fake](backend/app/llm/fake_client.py) | [Key-free fixtures](backend/tests/conftest.py), [socket blocking](backend/pyproject.toml), network-disabled Docker test command above. |
| **Total available** | **100** | | |

The offline tests establish application behavior with controlled replies. The live walkthrough below
checks real model output; it does not make model detection or review infallible.

## Live verification

On **November 18, 2026**, fresh real Foundry reports produced short model briefings and passed a
separate quality-review call on round 1. Each had three clear checks and seven trace cards,
including six calculated model-call costs. The visible briefing matched the exact saved model
summary, rather than the application-generated overview.

| Local report | Summary | Input / output tokens | Calculated cost | Verdict before / after approval |
| --- | --- | --- | --- | --- |
| [#9](http://localhost:8000/reports/9) | 39 words | 7,128 / 528 | $0.048840 USD | GO / GO |
| [#10](http://localhost:8000/reports/10) | 41 words | 7,242 / 505 | $0.048835 USD | NO-GO / NO-GO |

At the user's explicit request, authenticated browser actions approved both saved versions as
`demo-flight-director`; the pipeline itself left them pending. Findings, versions, costs, and
verdicts remained unchanged. These IDs refer to local verification records, not seeded demo data.
The temporary approval credential was not committed.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/reports` | Validate and save; return `202` with report ID and status URL. |
| `GET` | `/api/reports` | Newest-first summaries; `limit` 1–100 and `offset`. |
| `GET` | `/api/reports/{id}` | Report, checks, briefing versions, trace, costs, and approval audit. |
| `GET` | `/api/auth/me` | Verify the server-owned demo identity and role. |
| `POST` | `/api/reports/{id}/approve` | Authenticated Director approval of the current pending version. |
| `GET` | `/api/health` | Check API and database connectivity. |

Approval sends `run_id`, `briefing_version_id`, and `acknowledged_check_result_ids` from the latest
report. The ID list must contain exactly the flagged check IDs; use an empty list when none are
flagged. The server determines the approver identity. Request/response schemas and error details
are available at [/docs](http://localhost:8000/docs).

## Pricing and persistence

[.env.example](.env.example) contains the Opus 5 **Global Standard** rates verified on November 18,
2026: **$5 input, $0.50 cached input, $25 output, $6.25 five-minute cache writes, and $10 one-hour
cache writes per million tokens**. See [Foundry pricing](https://platform.claude.com/docs/en/about-claude/pricing#claude-in-microsoft-foundry-pricing)
and the [published rate card, page 15](https://assets.claude.com/a04aca860a54b7323e33f449706e9b43680e1346.pdf#page=15).
US Data Zone Standard uses a 1.1x multiplier; replace the rates for another model or negotiated price.
The local verification's non-secret prices also reside in an ignored `compose.override.yaml`;
Compose loads it automatically. Keep credentials out of that file.

Python calculates each step's token cost with `Decimal`, rounds to ten decimal places, and sums
across report attempts. Each trace stores provider usage and a pricing snapshot. Missing usage or
rates leave the full total unavailable; `known_cost_usd` is only a subtotal. Processing totals are
provisional. Estimates exclude non-token charges, taxes, and discounts; they are not Azure invoices.
Historical reports are never repriced after a configuration change.

SQLite data lives in the `mission_control_data` Docker volume. `docker compose down` preserves it;
adding `--volumes` deletes it. Keep **one app worker/container**: FastAPI `BackgroundTasks` is not
a durable queue. After a restart, unfinished runs become `failed` / `interrupted`, retaining saved
reports, verdicts, completed findings, drafts, and traces. Opening an old report never reruns it.

## Development

Optional host development requires uv and Python 3.12–3.14. Export configuration in the environment;
Compose reads `.env`, but the Python app does not load it itself.

```bash
uv run --directory backend uvicorn app.main:app --reload
uv run --directory backend pytest -q
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
```

Local data defaults to `backend/data/mission_control.db`; change it with `MISSION_CONTROL_DB_PATH`.
Direct Anthropic and OpenAI adapters remain explicit alternatives via `MODEL_PROVIDER`; providers
never switch automatically. The shared `.codex` credential guard has synthetic checks:
`python3 -m unittest discover -s .codex/hooks -p 'test_*.py'`. Review and trust hooks before relying
on them; they are guardrails, not OS isolation. The implementation roadmap is in [PLAN.md](PLAN.md).
