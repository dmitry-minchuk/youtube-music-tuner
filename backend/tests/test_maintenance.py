"""Backup and retention (docs/07 section 8, docs/09 section 8)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.jobs.maintenance import (
    create_backup,
    prune_backups,
    run_retention_cleanup,
)
from app.jobs.periodic import enqueue_due_jobs
from app.persistence.models import (
    ApiCallLedger,
    FeatureSnapshot,
    PlaybackSession,
    TelemetryEvent,
    utcnow,
)
from app.settings import get_settings


def add_event(db, client_event_id: str, age_days: int) -> None:
    db.add(
        TelemetryEvent(
            client_event_id=client_event_id,
            session_id=f"s-{client_event_id}",
            sequence_no=1,
            event_type="progress_tick",
            video_id="v1",
            occurred_at_client=utcnow() - dt.timedelta(days=age_days),
            received_at_server=utcnow() - dt.timedelta(days=age_days),
            monotonic_ms=1000,
        )
    )
    db.flush()


def test_backup_is_verified_and_has_a_manifest(migrated_data_dir, db_session) -> None:
    result = create_backup(get_settings())

    assert result.integrity_ok is True
    assert (result.path / "tuner.db").is_file()
    manifest = json.loads((result.path / "manifest.json").read_text())
    assert manifest["sha256"] == result.sha256
    assert manifest["integrityCheck"] == "ok"
    assert manifest["containsSecrets"] is False


def test_backup_never_contains_secret_files(migrated_data_dir) -> None:
    settings = get_settings()
    settings.secrets_dir.mkdir(parents=True, exist_ok=True)
    (settings.secrets_dir / "oauth.json").write_text('{"refresh_token": "secret"}')

    result = create_backup(settings)
    contents = {path.name for path in result.path.iterdir()}
    assert contents == {"tuner.db", "manifest.json"}


def test_old_raw_events_are_deleted_but_sessions_survive(migrated_data_dir, db_session) -> None:
    add_event(db_session, "fresh", age_days=10)
    add_event(db_session, "stale", age_days=200)
    db_session.add(
        PlaybackSession(
            session_id="ancient",
            video_id="v1",
            started_at=utcnow() - dt.timedelta(days=400),
            qualified=True,
            reward=0.5,
        )
    )
    db_session.flush()

    result = run_retention_cleanup(db_session, get_settings())

    assert result.raw_events == 1
    remaining = {row.client_event_id for row in db_session.query(TelemetryEvent).all()}
    assert remaining == {"fresh"}
    # The frozen historical summary must never be removed with its events.
    assert db_session.get(PlaybackSession, "ancient") is not None


def test_feature_snapshots_follow_raw_telemetry(migrated_data_dir, db_session) -> None:
    db_session.add(
        FeatureSnapshot(
            generation_id="g-old",
            video_id="v1",
            feature_schema_version="features-v1",
            features_json={},
            created_at=utcnow() - dt.timedelta(days=200),
        )
    )
    db_session.add(
        FeatureSnapshot(
            generation_id="g-new",
            video_id="v2",
            feature_schema_version="features-v1",
            features_json={},
            created_at=utcnow(),
        )
    )
    db_session.flush()

    result = run_retention_cleanup(db_session, get_settings())
    assert result.feature_snapshots == 1
    assert db_session.query(FeatureSnapshot).count() == 1


def test_ledger_rows_expire_after_ninety_days(migrated_data_dir, db_session) -> None:
    db_session.add(
        ApiCallLedger(operation="get_playlist", started_at=utcnow() - dt.timedelta(days=100))
    )
    db_session.add(ApiCallLedger(operation="get_playlist", started_at=utcnow()))
    db_session.flush()

    result = run_retention_cleanup(db_session, get_settings())
    assert result.ledger_rows == 1
    assert db_session.query(ApiCallLedger).count() == 1


def test_cleanup_refuses_to_run_on_a_bad_backup(migrated_data_dir, db_session) -> None:
    from app.jobs.maintenance import BackupResult

    broken = BackupResult(path=migrated_data_dir, sha256="x", size_bytes=1, integrity_ok=False)
    with pytest.raises(RuntimeError):
        run_retention_cleanup(db_session, get_settings(), backup=broken)


def test_backups_older_than_fourteen_days_are_pruned(migrated_data_dir) -> None:
    settings = get_settings()
    old = settings.backups_dir / "2020-01-01T00-00-00Z"
    old.mkdir(parents=True)
    (old / "tuner.db").write_text("stale")

    fresh = create_backup(settings)
    removed = prune_backups(settings)

    assert removed == 1
    assert not old.exists()
    assert fresh.path.exists()


# -- periodic scheduling --------------------------------------------------


def test_due_jobs_are_enqueued_once(migrated_data_dir, db_session) -> None:
    first = enqueue_due_jobs(db_session)
    assert "library_sync" in first
    assert "database_backup" in first

    # A second tick must not duplicate the queued work.
    assert enqueue_due_jobs(db_session) == []


def test_recent_sync_is_not_rescheduled(migrated_data_dir, db_session) -> None:
    from app.persistence.models import SyncRun

    db_session.add(
        SyncRun(
            sync_run_id="run-1",
            status="SUCCESS",
            started_at=utcnow(),
            finished_at=utcnow(),
        )
    )
    db_session.flush()

    assert "library_sync" not in enqueue_due_jobs(db_session)
