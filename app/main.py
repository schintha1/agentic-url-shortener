import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.errors import AppError, register_error_handlers
from app.logging_config import configure_logging, request_id_middleware
from app.orchestrator.routes import router as sdlc_router
from app.shortener.db import enable_wal, get_engine, make_session_factory
from app.shortener.models import Base
from app.shortener.rate_limit import SlidingWindowLimiter
from app.shortener.routes import router as shortener_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application (tests pass an isolated Settings)."""

    settings = settings or Settings()
    runs_path = Path(settings.runs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runs_path.mkdir(parents=True, exist_ok=True)
        app.state.runs_dir = str(runs_path)
        if settings.database_url.startswith("sqlite") and ":memory:" not in settings.database_url:
            Path("data").mkdir(parents=True, exist_ok=True)
        try:
            engine = get_engine(settings.database_url)
            Base.metadata.create_all(engine)
            enable_wal(engine)
            app.state.engine = engine
            app.state.session_factory = make_session_factory(engine)
        except (SQLAlchemyError, OSError):
            app.state.engine = None
            app.state.session_factory = None
        app.state.settings = settings
        app.state.limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)
        yield
        engine = getattr(app.state, "engine", None)
        if engine is not None:
            engine.dispose()

    configure_logging(getattr(logging, settings.log_level.upper(), logging.INFO))

    app = FastAPI(
        title="Agentic URL Shortener",
        version="0.2.0",
        description="URL shortener domain service with an agentic SDLC orchestrator.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)

    @app.get("/health", summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", summary="Readiness probe")
    def ready() -> dict[str, str]:
        engine = getattr(app.state, "engine", None)
        if engine is None:
            raise AppError(503, "not_ready", "database unavailable")
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise AppError(503, "not_ready", "database unavailable") from exc
        return {"status": "ready"}

    app.include_router(sdlc_router)
    app.include_router(shortener_router)
    return app


app = create_app()
