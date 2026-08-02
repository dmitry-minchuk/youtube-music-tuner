"""Scheduler pacing (docs/02 section 8).

A permanently failing job used to be re-queued on the very next poll, and
because the loop does not sleep after a job ran, that turned into a tight
spin: 331 job rows a minute, two and a half million rows and a 944 MB
database before anyone noticed.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from app.jobs import queue
from app.jobs.periodic import FAILURE_BACKOFF, enqueue_due_jobs
from app.jobs.scheduler import EXTERNAL_JOB_TYPES
from app.persistence.models import ApiCallLedger, Job, utcnow


def _finish(db, job: Job, status: str, when: dt.datetime) -> None:
    job.status = status
    job.finished_at = when
    job.updated_at = when
    db.flush()


def test_a_failed_job_is_not_requeued_immediately(db_session) -> None:
    now = utcnow()
    enqueue_due_jobs(db_session, now)
    job = db_session.scalar(
        select(Job).where(Job.job_type == queue.JOB_LIBRARY_SYNC, Job.status == "PENDING")
    )
    assert job is not None
    _finish(db_session, job, "FAILED", now)

    enqueue_due_jobs(db_session, now + dt.timedelta(seconds=5))
    pending = db_session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.job_type == queue.JOB_LIBRARY_SYNC, Job.status == "PENDING")
    )
    assert pending == 0


def test_the_job_returns_after_the_backoff(db_session) -> None:
    now = utcnow()
    enqueue_due_jobs(db_session, now)
    job = db_session.scalar(
        select(Job).where(Job.job_type == queue.JOB_LIBRARY_SYNC, Job.status == "PENDING")
    )
    assert job is not None
    _finish(db_session, job, "FAILED", now)

    later = now + FAILURE_BACKOFF + dt.timedelta(minutes=1)
    enqueue_due_jobs(db_session, later)
    pending = db_session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.job_type == queue.JOB_LIBRARY_SYNC, Job.status == "PENDING")
    )
    assert pending == 1


def test_training_without_a_new_snapshot_still_waits(db_session) -> None:
    """model_train succeeds without writing a snapshot when there is nothing
    to learn; the snapshot timestamp alone therefore never advances."""
    now = utcnow()
    enqueue_due_jobs(db_session, now)
    job = db_session.scalar(
        select(Job).where(Job.job_type == queue.JOB_MODEL_TRAIN, Job.status == "PENDING")
    )
    assert job is not None
    _finish(db_session, job, "SUCCEEDED", now)

    enqueue_due_jobs(db_session, now + dt.timedelta(seconds=5))
    pending = db_session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.job_type == queue.JOB_MODEL_TRAIN, Job.status == "PENDING")
    )
    assert pending == 0


def test_local_work_is_not_blocked_by_an_open_circuit() -> None:
    """Training, cleanup, backup and the rollup never call YouTube."""
    for job_type in (
        queue.JOB_MODEL_TRAIN,
        queue.JOB_RETENTION_CLEANUP,
        queue.JOB_DATABASE_BACKUP,
        queue.JOB_AFFINITY_ROLLUP,
    ):
        assert job_type not in EXTERNAL_JOB_TYPES

    for job_type in (
        queue.JOB_LIBRARY_SYNC,
        queue.JOB_CANDIDATE_REFRESH,
        queue.JOB_GRAPH_EXPAND,
        queue.JOB_PLAYLIST_PUBLISH,
        queue.JOB_RATING_SYNC,
    ):
        assert job_type in EXTERNAL_JOB_TYPES


def test_run_once_runs_local_work_while_the_circuit_is_open(db_session, fake_catalog) -> None:
    from app.jobs.scheduler import run_once
    from app.settings import get_settings

    now = utcnow()
    for index in range(4):
        db_session.add(
            ApiCallLedger(
                provider="ytmusic",
                operation="get_liked_songs",
                request_fingerprint="get_liked_songs",
                is_mutation=False,
                counts_against_automatic_budget=True,
                started_at=now - dt.timedelta(minutes=index + 1),
                finished_at=now,
                duration_ms=10,
                outcome="FAILED",
                status_class="YTM_AUTH_REQUIRED",
            )
        )
    queue.enqueue(db_session, queue.JOB_AFFINITY_ROLLUP, "affinity_rollup")
    db_session.commit()

    assert run_once(get_settings(), lambda _recorder: fake_catalog) is True

    db_session.expire_all()
    job = db_session.scalar(select(Job).where(Job.job_type == queue.JOB_AFFINITY_ROLLUP))
    assert job is not None
    assert job.status == "SUCCEEDED", job.last_error_code
