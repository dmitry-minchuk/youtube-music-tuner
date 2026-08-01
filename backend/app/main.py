"""FastAPI application: API plus the built frontend from a single container."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import health, library, system, telemetry, wave
from app.api.errors import ApiError, api_error_handler, unhandled_error_handler
from app.api.security import CspMiddleware, GuardMiddleware, SessionStore
from app.jobs.scheduler import Scheduler
from app.logging_config import configure_logging
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # uvicorn installs its own handlers after import; take them over again.
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "application starting",
        extra={"operation": "startup", "port": settings.port},
    )

    scheduler: Scheduler | None = None
    if app.state.scheduler_enabled:
        scheduler = Scheduler(settings, getattr(app.state, "catalog_factory", None))
        await scheduler.start()
        app.state.scheduler = scheduler

    yield

    if scheduler is not None:
        await scheduler.stop()
    logger.info("application stopping", extra={"operation": "shutdown"})


def create_app(settings: Settings | None = None, *, scheduler_enabled: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="YouTube Music Tuner",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.sessions = SessionStore()
    app.state.scheduler_enabled = scheduler_enabled
    app.state.catalog_factory = None

    app.add_middleware(CspMiddleware, settings=settings)
    app.add_middleware(GuardMiddleware, settings=settings, sessions=app.state.sessions)

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(library.router)
    app.include_router(telemetry.router)
    app.include_router(wave.router)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA when present; the API stays authoritative on /api."""
    if not FRONTEND_DIR.is_dir():
        logger.info("frontend bundle not found, serving API only")
        return

    assets = FRONTEND_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = FRONTEND_DIR / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIR / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIR):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
