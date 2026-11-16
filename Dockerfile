# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.14-slim AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/backend
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:$PATH" \
    MISSION_CONTROL_DB_PATH=/data/mission_control.db
WORKDIR /app/backend
RUN groupadd --gid 10001 mission && useradd --uid 10001 --gid mission --no-create-home mission \
    && mkdir /data && chown mission:mission /data
COPY --from=dependencies /app/backend/.venv ./.venv
COPY backend/app ./app
USER mission
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM dependencies AS test-dependencies
RUN uv sync --frozen --no-install-project

FROM runtime AS tests
COPY --from=test-dependencies /app/backend/.venv ./.venv
COPY backend/pyproject.toml ./
COPY backend/tests ./tests
ENV OPENAI_API_KEY="" MISSION_CONTROL_DB_PATH=/tmp/mission-control-tests.db
CMD ["pytest", "-q", "-p", "no:cacheprovider"]
