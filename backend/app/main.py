import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.reports import router
from app.config import Settings
from app.db.database import Database
from app.llm.client import ModelClient, ModelConfigurationError
from app.llm.factory import create_model_client
from app.services.pipeline import PipelineRunner, report_pipeline

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    runner: PipelineRunner | None = None,
    model_client: ModelClient | None = None,
) -> FastAPI:
    """Injected clients remain caller-owned; the app closes clients it constructs itself."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime_settings = settings or Settings.from_environment()
        database = Database(runtime_settings.database_path)
        owned_client = None
        try:
            database.initialize()
            application.state.database = database
            client = model_client
            if runner is None and client is None:
                try:
                    owned_client = client = create_model_client(runtime_settings)
                except ModelConfigurationError:
                    # API stays usable; each report records the explicit unknown/high fallback.
                    client = None
            application.state.pipeline_runner = runner or report_pipeline(client, runtime_settings)
            yield
        finally:
            try:
                if owned_client is not None:
                    await owned_client.aclose()
            finally:
                database.close()

    application = FastAPI(title="Mission Control", version="0.1.0", lifespan=lifespan)
    application.include_router(router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Exclude raw input: NaN/Infinity cannot be JSON-encoded and notes may be sensitive.
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @application.exception_handler(SQLAlchemyError)
    async def storage_error(request: Request, exc: SQLAlchemyError):
        logger.error("Database operation failed (%s)", type(exc).__name__)
        return JSONResponse(
            status_code=503, content={"detail": "Database temporarily unavailable."}
        )

    return application


app = create_app()
