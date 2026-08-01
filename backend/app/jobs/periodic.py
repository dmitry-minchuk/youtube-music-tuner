"""Periodic job scheduling (docs/02 section 8).

The scheduler enqueues due maintenance work; every job type keeps its own
cooldown so a restart cannot cause a burst of external calls.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs import queue
from app.jobs.library_sync import SYNC_TTL, last_successful_sync
from app.persistence.models import Job, ModelSnapshot, utcnow


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    job_type: str
    dedupe_key: str
    interval: dt.timedelta


SCHEDULE: tuple[PeriodicJob, ...] = (
    PeriodicJob(queue.JOB_LIBRARY_SYNC, "library_sync", SYNC_TTL),
    PeriodicJob(queue.JOB_CANDIDATE_REFRESH, "candidate_refresh", dt.timedelta(days=1)),
    PeriodicJob(queue.JOB_MODEL_TRAIN, "model_train", dt.timedelta(hours=1)),
    PeriodicJob(queue.JOB_RETENTION_CLEANUP, "retention_cleanup", dt.timedelta(days=1)),
    PeriodicJob(queue.JOB_DATABASE_BACKUP, "database_backup", dt.timedelta(days=1)),
)


def _last_run(db: Session, job_type: str) -> dt.datetime | None:
    return db.scalar(
        select(Job.finished_at)
        .where(Job.job_type == job_type, Job.status == "SUCCEEDED")
        .order_by(Job.finished_at.desc())
        .limit(1)
    )


def enqueue_due_jobs(db: Session, now: dt.datetime | None = None) -> list[str]:
    """Queue whatever is due. Already-active work is left alone."""
    now = now or utcnow()
    queued: list[str] = []

    for entry in SCHEDULE:
        if queue.find_active(db, entry.dedupe_key) is not None:
            continue

        last = _last_run(db, entry.job_type)
        if entry.job_type == queue.JOB_LIBRARY_SYNC:
            sync_run = last_successful_sync(db)
            last = sync_run.finished_at if sync_run else None
        if entry.job_type == queue.JOB_MODEL_TRAIN:
            snapshot = db.scalar(
                select(ModelSnapshot.created_at).order_by(ModelSnapshot.created_at.desc()).limit(1)
            )
            last = snapshot or last

        if last is not None and now - last < entry.interval:
            continue

        try:
            queue.enqueue(db, entry.job_type, entry.dedupe_key)
            queued.append(entry.job_type)
        except queue.JobAlreadyActive:
            continue

    return queued
