# Tool choices

The app uses one backend service, a persistent database volume, and three plain-HTML pages.
All model operations share one replaceable async interface.

| Layer | Choice and specific reason | Rejected alternative | Customer concern |
| --- | --- | --- | --- |
| Backend | Python + FastAPI: typed schemas validate reports and model replies; async services run the three checks concurrently and expose submission/polling endpoints. | Django adds account, admin, and full-stack features unnecessary for this small console. | A processing failure must preserve the report and explain the failure; approval permissions must be enforced on the server. |
| Frontend | Plain HTML/CSS and vanilla JavaScript, served by FastAPI: three pages need only forms, polling, tables, and disclosures, so the complete app starts without a frontend build. | React and a component library add dependencies and a build step beyond this assignment's UI needs. | The Director needs a short readable briefing, visible warnings, and inspectable evidence before approving. System typography, a wide layout, red failures, and amber flags support that workflow. |
| Database | SQLite + SQLAlchemy: transactions save reports, decisions, traces, and approval audits together; a Docker volume retains them without a separate database service. | PostgreSQL adds a service and operational setup unnecessary for the local single-worker application. | Data must survive restarts. This design supports one worker/container; multiple instances need different job ownership and recovery. |
| Model | Claude Opus 5 through Microsoft Foundry: the existing deployment supports schema-constrained classification, checks, and briefing calls through the Anthropic SDK. One async `ModelClient` isolates provider calls, reported usage, and offline fakes. | GPT-5.4 mini through OpenAI requires a separate provider/account instead of reusing the user's Foundry deployment. Its adapter remains an explicit alternative. | Deployment-specific prices must be configured for useful cost estimates. Output still needs validation and independent review; neither model role can change the numeric verdict or approve a briefing. |
| Delivery | Docker Compose: one command starts both API and frontend; another runs the isolated full test suite. | Host-managed services require Python/tool setup and make persistence less reproducible. | Initial builds need dependency downloads, but tests run without network or API keys and never mount the application data volume. |

The briefing writer and reviewer use separate calls to the same configured model, with distinct
instructions and schemas. The application owns the three-round cap and feeds rejected review
feedback into the next draft. An agent framework or two different models are not required.

The locked dependencies are in [backend/uv.lock](backend/uv.lock). Node's built-in runner is used
only for offline frontend tests in a Docker build stage; the runtime image has no Node dependency.
Temporary headless Chromium tooling verified browser navigation, layout, and approval; it is not
an application dependency. Scripted tests and real model verification are distinguished in
[README.md](README.md#live-verification).

Implementation references: [Foundry connection settings](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry),
[SQLite foreign keys](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support),
[FastAPI background-task sessions](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#background-tasks-and-dependencies-with-yield-technical-details),
and [uv's Docker workflow](https://docs.astral.sh/uv/guides/integration/docker/).
