# Tool choices

Implementation status: backend pipeline, deterministic rules, briefing review, accounting, and human approval.
Engineer form, standalone report page, and director console use plain HTML/CSS with minimal vanilla JavaScript.

| Layer | Choice and specific reason | Rejected alternative | Customer concern |
| --- | --- | --- | --- |
| Backend | Python + FastAPI; typed request/response schemas expose report submission and polling while async functions run concurrent checks and the bounded review loop. | Django adds full-stack features the current backend does not need. | Failed processing must preserve reports and explain its failure; server-side approval controls restrict the exact current briefing to the authenticated demo director. |
| Database | SQLite + SQLAlchemy 2.x; transactions atomically store report/decision/run/trace without operating a database server, and the Docker volume preserves data. | PostgreSQL requires an additional service for this local exercise. | One worker/container owns processing and recovery; multiple application instances would require a different ownership/queue design. |
| Frontend | Plain HTML/CSS and vanilla JavaScript served by FastAPI; separate submission/report pages and a director queue with selected details require no application frontend toolchain. White background, black text, and native controls keep the demo focused. | React and component libraries add dependencies/build steps beyond this small static-page scope. | Inspect findings, drafts, traces, and incomplete costs before explicit human approval; keep demo credentials in memory and reset acknowledgements after conflicts. |
| Model | Claude Opus 5 through Microsoft Foundry, selected by the user, behind the shared async `ModelClient`; the Foundry SDK uses Azure resource authentication and deployment names. | Direct Anthropic/OpenAI adapters remain explicit alternatives; neither is automatic fallback. LangChain/LangGraph add orchestration the bounded pipeline does not need. | Foundry deployment pricing must be configured explicitly. Live quality still needs evaluation; offline tests use fakes/mocked HTTP. Models never compute the verdict or approve briefings. |
| Delivery | Docker Compose packages the API and isolated test service; host Python and frontend tools are unnecessary for the primary commands. | Separate host-managed services make setup and persistence harder to reproduce. | Initial builds need dependency downloads, but tests must run offline; routine container replacement must preserve the named data volume. |

The current dependency lock uses the HTTP client required by the installed Starlette TestClient.
Foundry connection details follow [Anthropic's Foundry guide](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry), checked November 17, 2026; no paid live-model call was performed for this change.
Database connection configuration follows [SQLAlchemy's SQLite foreign-key guidance](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support).
Background jobs open their own sessions, following [FastAPI's background-task dependency guidance](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#background-tasks-and-dependencies-with-yield-technical-details).
Image dependency installation uses [uv's Docker workflow](https://docs.astral.sh/uv/guides/integration/docker/).

JavaScript workflow checks use Node 24's built-in runner only in Docker's test build stage, with networking disabled and no npm dependencies. The runtime target serves static files directly and contains no Node frontend runtime. Separate headless Chromium workflow checks and inspected desktop/mobile screenshots verified the final UI at four viewport widths. That browser tooling was temporary and is not an application dependency. Live model quality is not established by scripted fixtures.
