# Mission Control — Implementation Plan

**Project:** Meridian Launch Services — Go/No-Go Launch Console

**Goal:** Engineers submit anomaly reports; a backend pipeline validates readings, classifies and checks the note, produces a reviewed briefing, and leaves the final briefing approval to the Flight Director.

**Scope right now:** Implementation and final delivery verification complete, including the browser workflow and responsive UI cleanup. **Frontend: plain HTML, CSS, and small vanilla JavaScript files.** Delivery is a containerized full-stack application.

> This is an assignment/demo system, **not software certified for actual launch decisions**. A model's output must never override deterministic launch rules or approve a briefing.

## 1. Stack decisions

| Area | Choice | Reason |
| --- | --- | --- |
| Language | Python | Straightforward rules, concurrency, and model integration |
| API | FastAPI | Async endpoints, built-in request parsing, OpenAPI docs |
| Validation | Pydantic | Strong input and structured model-output schemas |
| Database | SQLite | Persistent local storage with no separate database server |
| ORM | SQLAlchemy 2.x | Explicit models, relationships, transactions, easy testing |
| AI | Claude Opus 5 through Microsoft Foundry behind `ModelClient`; optional direct adapters retained | User-selected provider, structured outputs, and a replaceable fake for offline tests |
| Concurrency | `asyncio.gather()` | Run the three independent note checks concurrently |
| Tests | pytest + pytest-asyncio + FastAPI TestClient/httpx | Fast, offline tests for API and pipeline behavior |
| Package management | uv | Reproducible project setup without installing into system Python |
| Packaging and startup | Docker + Docker Compose | One root-level command builds and starts the complete app, with persistent database storage |
| Frontend | Plain HTML + CSS + vanilla JavaScript | Small static pages; native forms and small scripts for API calls/polling; no framework or frontend build tool |

**Not using for the MVP:** Django (extra full-stack framework features we do not need), PostgreSQL (additional local service), LangChain/LangGraph (unnecessary orchestration abstraction), or a background job queue (premature for the exercise). Reassess if scope expands.

### 1.1 Containerized delivery (DECIDED)

- Root-level `compose.yaml`, `Dockerfile`, and `.dockerignore`; build context is the repository root; HTML/CSS/JavaScript will live in `backend/app/static/` and ship with the existing application copy.
- The default `app` service serves `/api/*` and the browser UI on `http://localhost:8000`. FastAPI serves submission, report, and director pages with local CSS/JavaScript assets. No Node/npm runtime, React, bundler, CDN, frontend compilation, or separate frontend service is needed. A test-only Node stage executes the dependency-free JavaScript workflow checks with networking disabled; it is excluded from the runtime target. Compose starts the complete app with the same command.
- Install Python dependencies from `backend/pyproject.toml` and `backend/uv.lock` using a frozen lockfile. The runtime working directory is `/app/backend`, so `app.main:app` resolves correctly. Keep dependency/build tooling in build stages where practical; the runtime contains the application and its installed dependencies.
- Run one Uvicorn worker, bound to `0.0.0.0:8000`, without auto-reload. The MVP uses one app container; do not scale it while interrupted-run recovery assumes exclusive ownership of processing jobs.
- Mount a named volume, `mission_control_data`, at `/data`; use `/data/mission_control.db` for SQLite. Ensure the runtime user can write there. Rebuilding/recreating the app or running `docker compose down` must preserve records; `down --volumes` is a deliberate data reset and is excluded from ordinary startup/shutdown instructions.
- Configure live credentials at runtime, never in image layers, frontend assets, or build arguments. Commit a root `.env.example` documenting configuration; exclude real `.env` files, credentials, databases, `.git`, and local virtual environments from the build context. A missing live API key is an explicit configuration error, never a silent switch to fake output.
- A separate `tests` service under the `test` profile uses a test image target with test dependencies, a temporary database, an explicit fake `ModelClient`, no live API key, and `network_mode: none`. It must not mount the application data volume or depend on the running app. The same command builds a test-only Node stage for offline JavaScript workflow checks and runs the Python suite in the isolated test container. [Compose profiles reference](https://docs.docker.com/compose/how-tos/profiles/).
- Docker Engine/Desktop with Compose is the host prerequisite; Python and uv run inside images; the static frontend requires no build tooling. Initial image builds may download dependencies. The test process itself must run offline once the image is built.

**Delivery commands, run from the repository root (API, all UI pages, and offline tests verified):**

```bash
docker compose up --build
docker compose --profile test run --build --rm --no-deps tests
```

The first command serves the API, engineer form, saved-report page, and Flight Director console. Headless Chromium workflow and responsive layout checks are verified; screenshots were inspected.

## 2. Non-negotiable behavior

1. **Verdict is code-only.** The verdict comes exclusively from three readings and cannot be modified by any model output, note, check, draft, or approval.
   - `wind_knots <= 25`
   - `temp_c >= 4`
   - `98 <= fuel_pct <= 100`
   - Any violation produces `no_go`; otherwise `go`. Record **all** violations.
2. **Bad input is rejected clearly.** Engineer name, station, and note are required; readings must be finite numeric values with wind >= 0, temperature >= -273.15°C, and fuel >= 0%. These input sanity minima apply before persistence. Readings outside the launch limits remain valid and produce `no_go`, including fuel above 100%; no arbitrary upper bounds are introduced.
3. **Severity controls deadline.** `high -> 5 min`, `medium -> 15 min`, `low -> 30 min`, measured from report creation. Any reading violation forces `high`, even if the classifier returns `low`.
4. **Classification is structured and validated.** Use a fixed subsystem enum (e.g. `propulsion`, `avionics`, `weather`, `ground_systems`, `other`, `unknown`) and severity (`high`, `medium`, `low`). Invalid/unavailable classification safely becomes `unknown` / `high`; retain a trace of the fallback.
5. **Three separate model checks run concurrently:** prompt-injection instructions, missing details, and panicked/alarmist tone. Keep their results distinct. Suspicious instructions must be flagged for human review and must not affect the verdict.
6. **Draft/review loop is bounded.** Model-generated briefing -> separate model review -> repeat with feedback only if needed, for **at most three drafts/reviews**. Save every draft and evaluator response. If no draft passes after three rounds, mark processing `failed` with reason `quality_review_exhausted`; no briefing becomes approvable until a successful reprocessing attempt.
7. **Only a person approves.** All briefings start unapproved. An authenticated Flight Director may approve only a `pending_review` report whose latest draft passed quality review and whose three checks completed. Flagged findings require explicit acknowledgement at approval; check errors or unresolved checks block approval. Approval is of the **briefing**, not a GO launch authorization, and never changes the code-calculated verdict.
8. **Every step is traceable.** Save step type, status, timestamps, token usage, and cost. Compute money in Python using provider-reported input/output tokens and configured per-model prices. Non-model steps have zero model tokens/cost. Mark unreported usage as unavailable rather than fabricating it.
9. **Offline tests always work.** The pipeline depends on one `ModelClient` abstraction, with a deterministic fake used by tests; no real API key, network, or charge is required.

## 3. High-level flow

```text
Engineer submits report
  -> Pydantic input validation
  -> Launch-limit evaluation in pure Python (immutable verdict/violations)
  -> Commit original report, verdict, violations, pipeline run, and rule trace
  -> Return 202 Accepted; continue in BackgroundTasks
  -> Model classification + schema validation/fallback
  -> Severity override + deadline calculation
  -> asyncio.gather(
       instruction-injection check,
       missing-details check,
       panic/tone check
     )
  -> Draft briefing (model)
  -> Evaluate briefing (separate model call)
  -> Revise using evaluator feedback, if needed (max 3 rounds total)
  -> Save latest briefing, check results, trace, and cost
  -> If all three checks completed and a draft passes quality: pending_review
  -> Otherwise: failed (report/verdict/trace remain visible)
  -> Flight Director acknowledges flagged findings and explicitly approves
```

### 3.1 Request lifecycle (DECIDED: asynchronous response + polling)

1. `POST /api/reports` validates input and computes the deterministic launch verdict. Commit the original report, verdict, violations, new `pipeline_run`, and deterministic rule trace in **one transaction**, before any external calls. Only after that commit, schedule the model pipeline and **return `202 Accepted`** with `{"report_id": ..., "status": "processing", "status_url": "/api/reports/{id}"}`. This response does not wait for model processing.
2. The backend processes the run after the response. The frontend polls `GET /api/reports/{id}` (initially every two seconds) while status is `processing`, stopping at `pending_review`, `failed`, or `approved`. That endpoint returns the verdict immediately, plus completed steps/checks/drafts as available. Before classification finishes, severity/deadline may be null unless a launch-limit breach already establishes `high` and `created_at + 5 minutes`; never present unknown classification as low severity. Calculate all final deadlines from the original report creation time.
3. For the local MVP, use FastAPI [`BackgroundTasks`](https://fastapi.tiangolo.com/tutorial/background-tasks/); **this design does not provide a durable queue**. On startup, before accepting requests, mark any persisted run left `processing` as `failed` with reason `interrupted`, update its report, and close unfinished trace steps as interrupted. Preserve completed results and recorded usage. This recovery assumes the single worker/container defined in Section 1.1. Do not automatically rerun interrupted work. An explicit retry/reprocess endpoint may be added later; a real deployment would use a durable worker queue.
4. Invalid form data returns `422` and saves nothing; persistence errors return `5xx` without scheduling work. Once a report is committed, scheduling/processing errors must record a failed state where storage is available; a process crash is recovered on startup. `GET` exposes the machine-readable reason and a human-readable explanation.
5. Pass only persisted report/run identifiers into background tasks and open a fresh database session there. Do not reuse the request session or share one SQLAlchemy session among concurrent checks. Run the three model calls concurrently, capture each result/error independently, and persist results in short transactions without holding database locks during network calls. Put finite configurable timeouts on every model call so polling cannot remain `processing` indefinitely; one check failure must not discard the other checks' findings.

### 3.2 Processing status, flags, and approval rules (DECIDED)

| Condition | Report status | Can Flight Director approve? |
| --- | --- | --- |
| Pipeline still running | `processing` | **No** |
| All three checks completed successfully as `clear` or `flagged`, without an evaluation error; latest briefing passed evaluation | `pending_review` | **Yes**, after explicitly acknowledging every flagged finding |
| Injection, missing-information, or panic check returns `flagged` but completes successfully | `pending_review` (assuming quality passes) | **Yes**, only with recorded acknowledgement; flag stays visible |
| Any required check is missing, raises an error, times out, or has an invalid response | `failed` | **No**; do not treat failure as `clear` |
| Drafting fails, evaluation call fails, or all three drafts fail quality review | `failed` | **No**; preserve drafts/feedback and failure reason |
| Classification returns an invalid value or its call fails | Continue with fallback `unknown` / `high`; warning in trace | **Potentially**, once all required checks and quality review succeed |
| Briefing already approved | `approved` | **No second approval** |

**Important distinction:** A successfully evaluated `flagged` check found something worth human attention; `error` means a check did not complete reliably. A malformed hidden-instructions reply also produces a visible `flagged` result, with `details_json.review_required = true` and a non-null `error_reason`. This fallback remains unresolved: fail the trace/run, block drafting and approval, and preserve the flag for review. Acknowledging it cannot turn an evaluation failure into a completed check. A `no_go` report may still have its **briefing** approved; approval never turns it into `go` or authorizes launch.

**Failure policy:** Keep the report, code-derived verdict, completed check results, failed step, draft history, and trace even when processing fails. Save a machine-readable failure reason. Do not quietly use fake output after a live model failure. A reprocessing attempt (when implemented) must create a fresh `pipeline_run` and must not mutate historical runs or approvals.

**Failure reasons:** At minimum distinguish `interrupted`, `scheduling_failed`, `required_check_failed`, `draft_failed`, `evaluation_failed`, and `quality_review_exhausted`. Classification fallback is a warning, not a failed run. If any required check errors (including a fail-safe injection flag with an evaluation-error marker), persist all three results and end the run as `failed` before drafting. A model review response that fails schema validation counts as `evaluation_failed`; a valid negative quality assessment consumes a round and supplies revision feedback.

## 4. Data model (initial schema)

Use **six tables** with foreign keys enabled and timestamps stored consistently in UTC. `check_results` is the authoritative source for persisted check findings; trace rows are for observability and must not be parsed to infer check state.

| Table | Core fields | Notes |
| --- | --- | --- |
| `reports` | `id`, `engineer_name`, `station`, `wind_knots`, `temp_c`, `fuel_pct`, `note`, `subsystem`, `severity`, `verdict`, `violations_json`, `brief_by_at`, `status`, `created_at`, `updated_at` | Keep original submission and code-derived verdict together; classification fields/deadline can be null while processing as described above |
| `pipeline_runs` | `id`, `report_id`, `status`, `failure_reason`, `model_name`, `total_input_tokens`, `total_output_tokens`, `total_cost_usd`, `started_at`, `completed_at` | One processing attempt; supports separate future retries |
| `check_results` | `id`, `run_id`, `check_type`, `outcome`, `reason`, `details_json`, `created_at` | At most one row per run/check; completed check stages contain all three; `check_type` = `injection`, `missing_details`, or `panic`; `outcome` = `clear`, `flagged`, or `error`; unique (`run_id`, `check_type`) |
| `trace_steps` | `id`, `run_id`, `step_name`, `status`, `input_tokens`, `output_tokens`, `cost_usd`, `details_json`, `started_at`, `completed_at` | One row per rule/classification/check/draft/evaluation step; model token/cost audit |
| `briefing_versions` | `id`, `report_id`, `run_id`, `round_number`, `content`, `evaluator_feedback`, `quality_passed`, `created_at` | Save all attempts and feedback; max three rounds per run |
| `approvals` | `id`, `report_id`, `briefing_version_id`, `approved_by`, `acknowledged_flags_json`, `approved_at` | Audit the exact version approved and flagged check results acknowledged |

**Check persistence:** Write all three `check_results` when the check stage completes, including errors. A completed check can be `clear` or `flagged`; if it cannot be evaluated, persist `error` rather than fabricating a pass, except malformed injection replies, which persist a fail-safe `flagged` result with an explicit evaluation-error marker. A crash can leave a subset of rows; restart recovery marks that run failed, and missing results never imply a clean check. Store human-readable reasons and optional structured details. The API can derive a summary of flags from these rows, but the rows—not report flags or trace JSON—are authoritative. Include the associated `run_id` in API results so retries cannot mix findings from different attempts.

**Statuses:** `processing`, `pending_review`, `approved`, `failed`. `failed` also has a machine-readable `pipeline_runs.failure_reason`. All briefings require an explicit human approval.

**State ownership:** Report status includes human approval. A pipeline run ends as `pending_review` or `failed` with `completed_at`; approving its briefing changes the report to `approved` and adds an approval record, without rewriting the completed processing history. Resolve the current run from persisted runs for that report using a deterministic creation order; all check/briefing lookups must use that run.

**Constraints:** Use database constraints for the check enums and unique (`run_id`, `check_type`), unique (`run_id`, `round_number`) with rounds 1–3, and unique `approvals.briefing_version_id`. Verify that an approved version belongs to the requested report and current run. Validate approval state and insert the audit record in one transaction so simultaneous approval requests cannot both succeed.

**Persistence:** SQLite lives in the named container volume at `/data/mission_control.db`. Stopping, rebuilding, or recreating the container must not delete reports. Tests use an isolated temporary database and never the application volume.

## 5. API contract (backend-first)

| Method | Endpoint | Responsibility |
| --- | --- | --- |
| `POST` | `/api/reports` | Validate and persist; compute the launch verdict; schedule processing; return **`202 Accepted`** with `report_id`, `status`, and `status_url` |
| `GET` | `/api/reports` | List reports with verdict, severity, briefing deadline, flags, status, cost |
| `GET` | `/api/reports/{report_id}` | Poll for status; return full report, violations, latest briefing, versions, checks, and step trace as available |
| `POST` | `/api/reports/{report_id}/approve` | Authenticated Flight Director approves the latest passing, pending briefing version and acknowledges flagged findings |
| `GET` | `/api/health` | Application health check |

Keep pipeline logic in services, not inside FastAPI route functions. Do not rely on clients preserving a connection until AI processing finishes. The report is committed before background processing starts.

**Approval rule:** Authenticate and authorize the Flight Director on the server; require `pending_review`, exactly three completed check results without `error`, a quality-passed latest briefing version, and the explicitly acknowledged IDs of **all** flagged findings. Verify that the requested version and run are the current ones; prevent duplicate/stale approvals. Persist approver identity from authentication plus acknowledgements. Never trust a client-provided `approved_by` as proof of identity. A clearly labeled local demo identity/session is acceptable for the assignment, not for production.

**Approval request:** `{"run_id": ..., "briefing_version_id": ..., "acknowledged_check_result_ids": [...]}`. Acknowledgements reference the flagged `check_results.id` values of that exact run; reject missing flags, unknown IDs, and IDs from another run/report. For a report with no flags, the list is empty. Return `401` for missing/invalid authentication, `403` for an authenticated non-director, `404` for an unknown report, `422` for malformed input, and `409` for state/version/acknowledgement conflicts. Success returns the persisted approval and report status `approved`, preserving the verdict.

## 6. Build milestones

### 6.1 Feature branch roadmap

Each branch delivers one focused change with its related tests and documentation. Create branches as work begins, in the order below; frontend branches use the selected plain-HTML approach.

| Order | Branch | Focus | Milestones |
| --- | --- | --- | --- |
| 1 | `feature/backend-foundation` | FastAPI setup, report submission/polling, SQLite schema and persistence, deterministic launch rules/deadlines, Docker foundation, restart recovery. Already implemented and pushed. | 1–2 |
| 2 | `feature/model-classification` | One replaceable `ModelClient`, OpenAI/fake adapters, model configuration, structured classification and fallback, effective severity/deadline persistence, model-call timeouts and usage traces. Implemented in two commits. | 3 |
| 3 | `feature/concurrent-checks` | Foundry provider setup (commit 1), then concurrent injection/missing-details/panic checks, per-run persistence, trace totals, and failure/recovery handling (commit 2). | 4 |
| 4 | `feature/briefing-review` | Shared credential guard/template, then briefing engine and API integration in separate commits. Generation, separate evaluation, feedback-driven revisions, three-round cap, version history, and transition to `pending_review` only when checks and quality pass. | 5 |
| 5 | `feature/trace-costs` | Two focused commits: pricing/math/configuration, then pipeline/API accounting. Per-step costs, run/report totals, unknown-usage handling, saved pricing, and trace latency. | 6 — accounting |
| 6 | `feature/director-approval` | Two focused commits: local demo bearer guard/configuration, then transactional approval/API tests. Current-version approval, flag acknowledgements, atomic audit, and duplicate/stale protection. | 6 — approval |
| 7 | `feature/report-submission-ui` | Two focused commits: plain-HTML engineer page/static serving, then offline workflow tests and Docker verification. Validation, confirmation, saved links, polling/retry, and shared minimal styling. | 7 — engineer workflow |
| 8 | `feature/director-console` | Three focused commits: queue and inspectable details; authenticated approval and acknowledgements; HTTP/restart tests and delivery evidence. Includes verdicts/deadlines, findings, briefing history, and trace cards/costs. | 7 — director workflow |
| 9 | `feature/final-verification` | Final delivery checks, dedicated report review pages, clean responsive UI/navigation, rubric coverage, and README evidence. | 8 |

**Branch workflow:**

- Confirm the preceding branch is merged, update `main`, and create the next branch from that updated `main`. Create future branches only when their work begins.
- If work is explicitly started while its dependency PR remains open, stack the branch on that dependency. Keep the two new commits separate and move onto updated `main` after the dependency merges.
- Keep commits focused within each branch. Include that feature's tests and necessary configuration/docs alongside its implementation; avoid unrelated cleanup or starting the next feature in the same branch.
- Record trace status and reported token usage as each model stage is introduced. The accounting branch completes pricing and aggregation; observability must not wait until then. Keep partial pipeline stages explicitly unapprovable until the required checks and briefing review are implemented.
- Update milestone checkboxes and the short README verification note as work is verified. Keep delivery evidence in `README.md`.

### Milestone 1 — Project foundation and persistence

- [x] Create FastAPI backend with `uv`, `pyproject.toml`, `.env.example`, and `.gitignore`.
- [x] Add root Dockerfile (runtime/test targets), `.dockerignore`, and Compose app/test services with persistent SQLite volume, single-worker startup, and health check. Verify the backend starts from the root Compose command; add UI assets in Milestone 7.
- [x] Add SQLAlchemy models (including `check_results`), SQLite initialization, session lifecycle, and foreign keys.
- [x] Add Pydantic input schemas and clear validation errors.
- [x] Implement the pure `evaluate_launch_limits()` foundation required by report creation; Milestone 2 completes its boundary coverage and severity/deadline functions.
- [x] Implement `POST /api/reports` returning `202`, and `GET /api/reports` / `GET /api/reports/{id}` for polling. Commit report, verdict, run, and rule trace atomically; wire background orchestration with fresh sessions, adding model stages in Milestones 3–5. Incomplete scaffolding must not publish an approvable briefing.
- [x] Test `202` response contract, polling status, invalid input, database isolation, restart persistence, and marking interrupted runs `failed` on restart.

**Current behavior:** Classification and three concurrent checks persist their results. Any check error fails the run as `required_check_failed`; successful checks proceed through at most three draft/evaluation rounds. A quality-passed briefing moves the run/report to `pending_review`; human approval is available through the role-protected endpoint. See the [README verification note](README.md#test).

**Done when:** The backend starts in Docker, a submitted report survives container recreation, interrupted runs become failed, and bad inputs give useful 4xx responses.

### Milestone 2 — Deterministic launch decisions

- [x] Complete/verify pure `evaluate_launch_limits()` returning `go`/`no_go` and all violated limits, introduced in Milestone 1.
- [x] Implement pure `resolve_severity()` and `calculate_brief_by()` functions.
- [x] Store the computed verdict and violations; immediately store high severity/deadline for limit breaches. In-limit reports retain null severity/deadline while classification is pending.
- [x] Test each inclusive boundary and just-outside value, combined violations, and severity override.

**Done when:** No model code is involved in verdict calculation and boundary tests pass.

### Milestone 3 — Model interface and classification

**Commit split (implemented):** Commit 1 adds the shared model interface, OpenAI adapter, scripted fake, configuration, and offline adapter tests. Commit 2 adds validated classification, fallback, pipeline integration, severity/deadline persistence, and usage traces. The pipeline stops explicitly before the required checks/review from later milestones.

- [x] Define one injectable async `ModelClient` interface for **all** model operations and wire classification through it.
- [x] Implement Anthropic/OpenAI adapters and a deterministic fake, with bounded calls, explicit refusal/incomplete results, and reported usage metadata.
- [x] Define and validate structured subsystem/severity responses with Pydantic.
- [x] Fail safely on malformed output, unrecognized enum values, and call failures.
- [x] Connect classification to effective severity and persist its deadline from original report creation, using the tested Milestone 2 helpers.
- [x] Record model usage for each completed call.
- [x] Select `gpt-5.4-mini-2026-03-17` and document configurable pricing. Store the actual model identifier, applicable rates, and usage availability per model trace step. Feature 5 now calculates costs; historical unpriced traces remain unavailable. Adapter behavior is tested with mocked HTTP; scripted tests do not measure live-model quality.

**Provider update:** Claude Opus 5 now runs through Microsoft Foundry: `MODEL_PROVIDER=foundry`, `ANTHROPIC_FOUNDRY_API_KEY`, and either a Foundry resource name or `/anthropic/` base URL. `ANTHROPIC_MODEL` must match the deployment name. Foundry rates are deployment configuration, not inferred from direct Anthropic pricing. Optional direct adapters never act as automatic fallback.

**Done when:** Invalid model classification falls back to `unknown`/`high` without affecting the verdict; tests require no key.

### Milestone 4 — Concurrent safety checks

- [x] Write three separate note-check prompts/schemas: injected instructions, missing details, panic/tone.
- [x] Run them together with `asyncio.gather()` and persist a `check_results` row for each, including `clear`/`flagged`/`error` and reason.
- [x] Treat report notes as untrusted data, never as control instructions.
- [x] Keep injection findings `flagged` for future human acknowledgement; required-check errors fail the run. Approval itself remains Milestone 6.
- [x] Capture per-check exceptions, timeouts, and invalid output without losing sibling results; persist all three outcomes and fail before drafting if any check errors.
- [x] Test true concurrency using coordinated fake calls, simple/forged-authority injection fixtures, partial polling, and crash recovery. These test pipeline behavior; live detection quality is not measured by scripted responses.

**Done when:** The injection attempt is flagged, and a wind reading of `26` still yields `no_go`.

### Milestone 5 — Briefing generation and quality review

- [x] Draft a 20–45 word narrative without a minimum length with canonical facts supplied separately. Enforce an 80-word maximum in code; reject internal jargon, repeated sentences, and three-reading recitals. Feed readability rejections into the same three-round revision loop, even if the model reviewer passes the draft.
- [x] Validate that generated text cannot replace the canonical verdict/violations stored by Python.
- [x] Evaluate each draft using a separate model call with structured feedback.
- [x] Feed evaluator comments to the next drafting round, stopping on pass or after three rounds.
- [x] Persist each version, feedback, and whether the latest draft passed quality review. If all three fail, set status `failed` and block approval.

**Model roles:** A writer and reviewer make separate calls through the shared `ModelClient`, with distinct prompts/schemas and no shared conversation. Both currently use the configured Foundry model; different models or autonomous agents are not rubric requirements. Only the human approval endpoint can approve.

**Implementation:** Strict structured drafts carry a summary and copies of canonical facts. Python renders the saved facts and all flagged findings into each briefing; any differing model fact forces rejection even when the evaluator reports a pass. The independent evaluator checks narrative accuracy and quality; this does not guarantee perfect model judgment. Each draft response and version, and each evaluation response and feedback, commit together. Invalid/refused/incomplete responses and timeouts fail explicitly, retaining available usage and prior versions. Interrupted evaluations preserve an unapproved draft; late responses cannot promote stale or terminated runs. Feature 5 adds accounting; Feature 6 now supplies explicit human approval.

**Done when:** A fake evaluator that fails twice and passes once produces exactly three rounds with feedback carried forward; an always-failing evaluator stops at three.

### Milestone 6 — Human approval, trace, and costs

- [x] Implement role-protected approval of the current pending briefing.
- [x] Block approval of missing, errored/unresolved-check, quality-failed, already approved, or stale briefing versions; require recorded acknowledgement of every flagged finding.
- [x] Persist trace steps for both code and model operations, including status, timestamps, token counts, and derived latency.
- [x] Calculate per-step and report-total costs using configurable provider rates and reported token usage; handle missing usage explicitly.
- [x] Return authoritative check results, trace, flags, current-run totals, per-run history, and report-wide cost totals/subtotals through the API.
- [x] Return persisted human approval through the API.
- [x] Test token/cost math, cache categories, billed failures, unavailable usage/prices, provisional subtotals, multiple attempts, saved rates, and restart recovery.
- [x] Test unauthorized approval, no-go briefing approval without altering verdict, flagged-check acknowledgements, unresolved-check/failed-quality blocks, correct version, audit record, rollback, concurrent requests, and restart persistence.

**Accounting implemented:** Token-cost estimates use Python `Decimal`, round each step to ten decimal places, and persist the applied rates and component amounts. Normalized Claude input includes base/cache reads/cache writes, with raw usage retained separately. Missing usage or needed rates leave amounts null; known subtotals remain visible. Costs are provisional until a run terminates, and interrupted model calls stay unavailable. Report totals cover every attempt without mixing historical checks or briefings into the current run. Existing unpriced history is not repriced on reads. Foundry rates, including optional per-TTL cache-write rates, are deployment configuration.

**Approval implemented:** A server-configured demo bearer token selects a fixed Flight Director identity; a separate optional engineer token exercises role denial. Blank tokens grant no access. This guard and `/api/auth/me` helper are implementation choices, not assignment mandates; account management is outside this feature. Approval checks the current run/version, complete checks, quality result, and exact flag IDs. A conditional status update is the first SQL statement, acquiring SQLite's writer lock before validation reads. All checks and the approval audit share that transaction; conflicts or storage errors roll back the status change. Concurrent requests admit one winner. The report becomes `approved`; processing history, costs, flags, and verdict remain unchanged.

**Done when:** The backend can show what happened, what it cost, and who approved, without any model being able to approve. Backend accounting, approval, and browser integration are implemented.

### Milestone 7 — Frontend and final demonstration

- [x] Select plain HTML/CSS with minimal vanilla JavaScript for the small static-page UI.

**UI decision:** Use the apple-design skill’s hierarchy, system typography, restraint, immediate feedback, and accessibility principles. Full-width report review uses a main column and sidebar, a single checks table, and compact trace cards with expandable audit details. Keep neutral surfaces; reserve red for failures/NO-GO and amber for review warnings, always paired with labels. Summaries lead, detailed findings and raw metadata expand on demand. Honor larger text and reduced motion. No component library, frontend build system, charts, or decorative animation.

1. **Engineer page (`/`):** Show the input form and validation errors. A successful submission navigates to its separate report page.
2. **Report review page (`/reports/{id}`):** Use the full viewport width with the briefing, a single checks table, and compact trace cards in the main column; readings, deadline, approval, and costs occupy a sidebar. Keep complete findings, timestamps, raw execution data, original input, and saved versions accessible through disclosures. New summaries are limited to 80 words; long or jargon-heavy historical versions show a clearly labelled overview from the saved verdict/checks; their original model briefing and approval version remain unchanged. Keep polling and GET-only retry here. All reports returns to the queue; New report returns to the form. Put approval access in a collapsed panel for pending briefings and keep its token in page memory. Failed reports show unavailable stages and no approval form.
3. **Report queue (`/reports`):** Show the paginated list only. Use normal links to open `/reports/{id}` as a new document, with working browser Back/Forward. Old `/?report={id}`, `/director?report={id}`, and `/reports?report={id}` bookmarks redirect to the dedicated report page. Old `/director` without an ID redirects to the queue.

Use small local JavaScript files for `fetch`, form submission, selection, and two-second polling while processing. Render untrusted report/model text as text, not executable HTML. The pages and assets ship with the Python app; no frontend compilation or external asset downloads are required.

- [x] Build the engineer form with field errors and redirect successful submissions to a separate saved-report page.
- [x] Build the report queue at `/reports` with normal links to dedicated review pages at `/reports/{id}`; preserve legacy links with redirects.
- [x] Display verdict, violations, severity, deadline, check flags, briefing versions, trace cards, token counts, and costs.
- [x] Add explicit human approval interaction through the protected API, keeping demo credentials only in memory.
- [x] The report page polls while processing, stops at terminal states, shows failures/unavailable classification, and offers GET-only status retries. Duplicate submits and stale responses are guarded.
- [x] Director page displays provisional costs and requires explicit flag acknowledgement before approval. Conflicts refresh the report and reset acknowledgements; interrupted POSTs are never retried automatically.
- [x] Package the engineer HTML/CSS/JS in the existing runtime image; verify UI/assets, report-link reload, `/api`, and `/docs` coexist. The isolated production-image smoke test reached healthy status.
- [x] Complete the director page so the root Compose command exposes the entire browser workflow.
- [x] Verify the full workflow, restart persistence, and responsive layout in headless Chromium; inspect desktop/mobile screenshots.

**Feature 7 evidence:** 423 Python tests plus 26 JavaScript workflow checks pass locally and through the offline Docker test command. The JavaScript harness uses an in-memory DOM; feature 9 adds separate real-browser verification.

**Feature 8 evidence:** 427 Python tests and 52 JavaScript checks pass locally and through the offline Docker test command. Real HTTP tests cover flagged GO/NO-GO briefing approval, permission and acknowledgement rejection, unchanged verdict/history/costs, and approval persistence after restart. The isolated production image serves all pages and assets. Feature 9 adds real-browser workflow/layout verification; the in-memory DOM tests alone do not establish visual correctness.

**Done when:** All information required by the rubric can be viewed and the approval performed from the browser.

### Milestone 8 — Documentation and grading verification

- [x] Document one root-level Docker Compose command to start the complete app and one to run all tests, with prerequisites, runtime configuration, browser URL, and offline-test details in `README.md`.
- [x] Complete `TOOL_CHOICES.md` with backend, frontend, database, and model reasons, rejected alternatives, and customer concerns.
- [x] Verify all tests without live credentials or internet access: 436 Python tests and 58 JavaScript checks pass locally and in network-disabled Docker stages; lint/formatting and 11 credential-hook tests pass.
- [x] Verify Compose configuration, clean runtime/test image builds, app health, all pages/assets, and complete report persistence after container recreation. Kill a separate fake-model container during evaluation, then recover its volume using the production image; preserve original verdict, completed findings/draft/traces, and known costs as `failed` / `interrupted`.
- [x] Hold classification on a test-owned gate, receive real HTTP `202`, inspect the processing report, release the gate, and poll the complete pipeline to `pending_review` without automatic approval.
- [x] Verify boundary/deadline, classifier fallback, concurrent checks, injection, three-round feedback/cap, approval permissions, trace, cost, and restart coverage against `ASSIGNMENT.md`.
- [x] Run a browser demo of wind 25 and 25.01 knots through separate report pages, native queue-to-detail navigation, Back/Forward, sign-in, explicit flag acknowledgement, and approval. GO/NO-GO remain unchanged. Inspect screenshots and check form/queue/review layouts at 320, 390, 768, 1440, and 1920 pixels; also verify 200% text and reduced motion. This is an automated Chromium walkthrough with visual inspection, not a live-model quality evaluation.

**Feature 9 evidence:** See the [README verification note](README.md#test). Browser tooling and screenshots are temporary verification artifacts; application dependencies and the runtime image remain unchanged.

**Optional backend-only development commands (run from the repository root):**

```bash
uv run --directory backend uvicorn app.main:app --reload
uv run --directory backend pytest -q
```

These optional commands require host uv and are not the full-app delivery commands. The Docker commands in Section 1.1 are the primary documented start/test interface.

## 7. Backend layout (key files)

```text
mission-control/
├── PLAN.md
├── README.md
├── TOOL_CHOICES.md
├── compose.yaml              # app + isolated tests profile; named data volume
├── Dockerfile                # backend runtime/tests; copies static UI with app code
├── .dockerignore
├── .env.example              # runtime configuration; no credentials
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── app/
│   │   ├── main.py
│   │   ├── static/            # plain HTML/CSS/JS pages
│   │   │   ├── index.html
│   │   │   ├── report.html
│   │   │   ├── queue.html      # report list; links open dedicated review pages
│   │   │   └── assets/
│   │   │       ├── styles.css
│   │   │       ├── common.js
│   │   │       ├── report-view.js
│   │   │       ├── report.js
│   │   │       ├── submission.js
│   │   │       └── queue.js    # queue pagination and refresh
│   │   ├── api/
│   │   │   ├── reports.py
│   │   │   ├── pages.py
│   │   │   └── auth.py
│   │   ├── db/
│   │   │   ├── database.py
│   │   │   └── models.py
│   │   ├── schemas/
│   │   │   ├── reports.py
│   │   │   ├── checks.py
│   │   │   ├── briefings.py
│   │   │   └── approvals.py
│   │   ├── llm/
│   │   │   ├── client.py
│   │   │   ├── factory.py
│   │   │   ├── anthropic_client.py
│   │   │   ├── openai_client.py
│   │   │   └── fake_client.py
│   │   └── services/
│   │       ├── launch_rules.py
│   │       ├── classification.py
│   │       ├── safety_checks.py
│   │       ├── briefings.py
│   │       ├── approvals.py
│   │       ├── costs.py
│   │       └── pipeline.py
│   └── tests/
│       ├── test_reports_api.py
│       ├── test_launch_rules.py
│       ├── test_classification.py
│       ├── test_safety_checks.py
│       ├── test_briefings.py
│       ├── test_approvals.py
│       ├── test_costs.py
│       ├── test_persistence.py
│       ├── test_pages.py
│       └── frontend/          # dependency-free queue/review/submission checks
```

## 8. Definition of done

- [x] From the repository root, one documented Docker Compose command builds/starts the complete app and one runs all tests; both have recorded evidence.
- [x] Container recreation/rebuild preserves SQLite data in a named volume; interrupted processing fails explicitly on startup under the single-worker constraint.
- [x] Report submission rejects malformed input and persists data through restart.
- [x] All exact launch limit boundaries and just-outside values are tested.
- [x] Deadline matches effective severity; any limit breach forces high.
- [x] Model classification has validated structured output and safe fallback.
- [x] All three checks execute concurrently; hidden instructions are flagged and cannot alter verdict.
- [x] Draft/evaluator loop has feedback, never exceeds three rounds, and blocks approval if quality never passes.
- [x] A human-only backend action approves a pending, quality-passed briefing after flag acknowledgements; unresolved checks block approval.
- [x] Each step exposes tokens and calculated costs, with test coverage; authoritative per-run check results are stored separately from traces.
- [x] Tests pass offline with fake model and no API key; POST returns `202`, and GET polling shows a terminal state.
- [x] `TOOL_CHOICES.md` includes reasons, rejected alternatives, and customer concerns for all four layers.

**Next step:** Review the completed `feature/final-verification` branch for merge.
