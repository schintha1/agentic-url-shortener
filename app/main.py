from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.errors import AppError, register_error_handlers


def _sqlite_connect_args(url: str) -> dict[str, bool]:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def check_database(database_url: str) -> None:
    engine = create_engine(database_url, connect_args=_sqlite_connect_args(database_url))
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise AppError(503, "not_ready", "database unavailable") from exc
    finally:
        engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application (tests pass an isolated Settings)."""

    settings = settings or Settings()
    runs_path = Path(settings.runs_dir)
    data_dir = Path("data")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runs_path.mkdir(parents=True, exist_ok=True)
        if settings.database_url.startswith("sqlite") and "memory" not in settings.database_url:
            data_dir.mkdir(parents=True, exist_ok=True)
        app.state.settings = settings
        yield

    app = FastAPI(
        title="Agentic URL Shortener",
        version="0.1.0",
        description="URL shortener domain service with an agentic SDLC orchestrator.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    register_error_handlers(app)

    @app.get("/health", summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", summary="Readiness probe")
    def ready() -> dict[str, str]:
        check_database(settings.database_url)
        return {"status": "ready"}

    return app


app = create_app()
