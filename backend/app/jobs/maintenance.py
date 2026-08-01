"""Retention cleanup and database backup (docs/07 section 8, docs/09 s.8).

Cleanup only runs after a successful backup, and it never removes session
aggregates together with their raw events: the summary stays as the frozen
historical record once the raw window has passed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    ApiCallLedger,
    CandidateEdge,
    FeatureSnapshot,
    Job,
    TelemetryEvent,
    utcnow,
)
from app.settings import Settings

logger = logging.getLogger(__name__)

RAW_EVENT_RETENTION_DAYS = 180
FEATURE_SNAPSHOT_RETENTION_DAYS = 180
LEDGER_RETENTION_DAYS = 90
FAILED_JOB_RETENTION_DAYS = 30
CANDIDATE_EDGE_RETENTION_DAYS = 365
BACKUP_RETENTION_DAYS = 14


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    sha256: str
    size_bytes: int
    integrity_ok: bool


@dataclass(frozen=True, slots=True)
class CleanupResult:
    raw_events: int
    feature_snapshots: int
    ledger_rows: int
    failed_jobs: int
    candidate_edges: int
    backups_removed: int


def create_backup(settings: Settings, now: dt.datetime | None = None) -> BackupResult:
    """Consistent VACUUM INTO copy plus a checksummed manifest."""
    now = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    target_dir = settings.backups_dir / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "tuner.db"

    source = sqlite3.connect(settings.database_path)
    try:
        source.execute("VACUUM INTO ?", (str(target),))
    finally:
        source.close()

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    verifier = sqlite3.connect(target)
    try:
        integrity = verifier.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        verifier.close()

    manifest = {
        "createdAt": now.isoformat() + "Z",
        "sha256": digest,
        "sizeBytes": target.stat().st_size,
        "integrityCheck": integrity,
        "containsSecrets": False,
    }
    (target_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return BackupResult(
        path=target_dir,
        sha256=digest,
        size_bytes=target.stat().st_size,
        integrity_ok=integrity == "ok",
    )


def prune_backups(settings: Settings, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
    cutoff = now - dt.timedelta(days=BACKUP_RETENTION_DAYS)
    removed = 0
    if not settings.backups_dir.is_dir():
        return 0
    for entry in settings.backups_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            created = dt.datetime.strptime(entry.name, "%Y-%m-%dT%H-%M-%SZ")
        except ValueError:
            continue
        if created < cutoff:
            for child in entry.iterdir():
                child.unlink()
            entry.rmdir()
            removed += 1
    return removed


def run_retention_cleanup(
    db: Session,
    settings: Settings,
    *,
    now: dt.datetime | None = None,
    backup: BackupResult | None = None,
) -> CleanupResult:
    """Delete expired rows — only ever after a verified backup."""
    now = now or utcnow()
    if backup is None:
        backup = create_backup(settings, now)
    if not backup.integrity_ok:
        raise RuntimeError("refusing to clean up: the backup failed its integrity check")

    raw_cutoff = now - dt.timedelta(days=settings.raw_event_retention_days)
    raw_events = db.execute(
        delete(TelemetryEvent).where(TelemetryEvent.received_at_server < raw_cutoff)
    ).rowcount

    # Feature snapshots follow raw telemetry; session summaries never do.
    feature_cutoff = now - dt.timedelta(days=FEATURE_SNAPSHOT_RETENTION_DAYS)
    features = db.execute(
        delete(FeatureSnapshot).where(FeatureSnapshot.created_at < feature_cutoff)
    ).rowcount

    ledger = db.execute(
        delete(ApiCallLedger).where(
            ApiCallLedger.started_at < now - dt.timedelta(days=LEDGER_RETENTION_DAYS)
        )
    ).rowcount

    failed_jobs = db.execute(
        delete(Job).where(
            Job.status.in_(("FAILED", "CANCELLED")),
            Job.updated_at < now - dt.timedelta(days=FAILED_JOB_RETENTION_DAYS),
        )
    ).rowcount

    # An edge records that YouTube considers two tracks related, which is a
    # fact about the catalogue rather than a cache entry: it is kept long
    # after ``expires_at`` says the seed may be queried again. Only edges that
    # nobody refreshed for a year are dropped.
    edges = db.execute(
        delete(CandidateEdge).where(
            CandidateEdge.fetched_at < now - dt.timedelta(days=CANDIDATE_EDGE_RETENTION_DAYS)
        )
    ).rowcount

    removed_backups = prune_backups(settings, now)
    db.flush()

    result = CleanupResult(
        raw_events=raw_events,
        feature_snapshots=features,
        ledger_rows=ledger,
        failed_jobs=failed_jobs,
        candidate_edges=edges,
        backups_removed=removed_backups,
    )
    logger.info(
        "retention cleanup finished",
        extra={
            "operation": "retention_cleanup",
            "outcome": "SUCCESS",
            **asdict(result),
        },
    )
    return result


def sessions_are_preserved(db: Session) -> int:
    """Sanity helper: cleanup must never touch playback session summaries."""
    from app.persistence.models import PlaybackSession

    return len(db.scalars(select(PlaybackSession.session_id)).all())
