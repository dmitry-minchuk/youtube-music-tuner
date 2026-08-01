"""Health endpoints (docs/02 section 11, docs/09 section 7).

Liveness never touches YouTube or Google. Readiness checks the database,
the applied schema version and that the data directory is writable — an
external outage must not make the app look unready.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import inspect, text

from app.persistence.database import get_engine
from app.settings import get_settings

router = APIRouter(tags=["health"])

EXPECTED_TABLES = ("schema_metadata", "tracks", "telemetry_events", "jobs")


def _readiness_report() -> tuple[bool, dict[str, Any]]:
    settings = get_settings()
    checks: dict[str, Any] = {}
    ok = True

    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
        missing = [name for name in EXPECTED_TABLES if name not in tables]
        checks["database"] = "ok" if not missing else f"missing tables: {', '.join(missing)}"
        checks["migrations"] = "ok" if "alembic_version" in tables else "not applied"
        ok = ok and not missing and "alembic_version" in tables
    except Exception as exc:  # pragma: no cover - defensive
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False

    writable = os.access(settings.data_dir, os.W_OK)
    checks["dataDirWritable"] = writable
    ok = ok and writable

    return ok, checks


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(response: Response) -> dict[str, Any]:
    ok, checks = _readiness_report()
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}
