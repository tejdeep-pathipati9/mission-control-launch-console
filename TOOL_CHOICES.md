# Tool choices

Implementation status: backend foundation and deterministic rules. Frontend/model decisions
remain open and must be completed before the final assignment demonstration.

| Layer | Choice and specific reason | Rejected alternative | Customer concern |
| --- | --- | --- | --- |
| Backend | Python + FastAPI; typed request/response schemas expose report submission and polling while async functions support the planned concurrent checks. | Django adds full-stack features the current backend does not need. | Failed processing must preserve reports and explain its failure; server-side approval controls are a later milestone. |
| Database | SQLite + SQLAlchemy 2.x; transactions atomically store report/decision/run/trace without operating a database server, and the Docker volume preserves data. | PostgreSQL requires an additional service for this local exercise. | One worker/container owns processing and recovery; multiple application instances would require a different ownership/queue design. |
| Frontend | TBD after reviewing the working API; prefer assets packaged into the app image. | Framework alternatives have not been evaluated yet. | The director needs visible flags, deadlines, trace cards, and explicit approval; the final stack must support the single-command startup. |
| Model | OpenAI behind one replaceable `ModelClient` is planned; exact model/pricing are TBD. No live adapter is installed in this milestone. | LangChain/LangGraph are excluded because the bounded pipeline does not need an orchestration framework; an alternative model/provider still needs evaluation. | Predictable costs, data handling, and reliable structured output must be assessed before live acceptance. The model must never compute the verdict or approve a briefing. |
| Delivery | Docker Compose packages the API and isolated test service; host Python and frontend tools are unnecessary for the primary commands. | Separate host-managed services make setup and persistence harder to reproduce. | Initial builds need dependency downloads, but tests must run offline; routine container replacement must preserve the named data volume. |

The current dependency lock uses the HTTP client required by the installed Starlette TestClient.
Database connection configuration follows [SQLAlchemy's SQLite foreign-key guidance](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support).
Background jobs open their own sessions, following [FastAPI's background-task dependency guidance](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#background-tasks-and-dependencies-with-yield-technical-details).
Image dependency installation uses [uv's Docker workflow](https://docs.astral.sh/uv/guides/integration/docker/).
