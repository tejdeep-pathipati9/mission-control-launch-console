# Tool choices

Implementation status: backend foundation, deterministic rules, and model classification.
Frontend selection remains open before the final assignment demonstration.

| Layer | Choice and specific reason | Rejected alternative | Customer concern |
| --- | --- | --- | --- |
| Backend | Python + FastAPI; typed request/response schemas expose report submission and polling while async functions support the planned concurrent checks. | Django adds full-stack features the current backend does not need. | Failed processing must preserve reports and explain its failure; server-side approval controls are a later milestone. |
| Database | SQLite + SQLAlchemy 2.x; transactions atomically store report/decision/run/trace without operating a database server, and the Docker volume preserves data. | PostgreSQL requires an additional service for this local exercise. | One worker/container owns processing and recovery; multiple application instances would require a different ownership/queue design. |
| Frontend | TBD after reviewing the working API; prefer assets packaged into the app image. | Framework alternatives have not been evaluated yet. | The director needs visible flags, deadlines, trace cards, and explicit approval; the final stack must support the single-command startup. |
| Model | OpenAI `gpt-5.4-mini-2026-03-17` behind one async `ModelClient`; a versioned model supporting structured outputs fits the fixed classification schema. Standard rates per million tokens: $0.75 input, $0.075 cached input, $4.50 output; configurable and snapshotted in traces. | A larger GPT-5.4 model adds expense for this short classification task. LangChain/LangGraph add orchestration the bounded pipeline does not need. | Live quality still needs evaluation; offline tests verify schema/fallback behavior through fakes and mocked HTTP. The model never computes the verdict or approves a briefing. |
| Delivery | Docker Compose packages the API and isolated test service; host Python and frontend tools are unnecessary for the primary commands. | Separate host-managed services make setup and persistence harder to reproduce. | Initial builds need dependency downloads, but tests must run offline; routine container replacement must preserve the named data volume. |

The current dependency lock uses the HTTP client required by the installed Starlette TestClient.
Model capabilities and standard pricing were checked against [OpenAI's model page](https://developers.openai.com/api/docs/models/gpt-5.4-mini) on November 16, 2026; no paid live-model call was performed for this feature.
Database connection configuration follows [SQLAlchemy's SQLite foreign-key guidance](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support).
Background jobs open their own sessions, following [FastAPI's background-task dependency guidance](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#background-tasks-and-dependencies-with-yield-technical-details).
Image dependency installation uses [uv's Docker workflow](https://docs.astral.sh/uv/guides/integration/docker/).
