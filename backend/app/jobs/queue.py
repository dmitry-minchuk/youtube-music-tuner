"""Job queue with dedupe keys and leases (docs/02 section 8).

One active job per dedupe key is enforced by a partial unique index, so even
a misconfigured second process cannot publish the same playlist twice.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.persistence.models import Job, utcnow

LEASE_DURATION = dt.timedelta(minutes=10)
ACTIVE_STATUSES = ("PENDING", "RUNNING")

JOB_LIBRARY_SYNC = "library_sync"
JOB_CANDIDATE_REFRESH = "candidate_refresh"
JOB_MODEL_TRAIN = "model_train"
JOB_PLAYLIST_PUBLISH = "playlist_publish"
JOB_RETENTION_CLEANUP = "retention_cleanup"
JOB_DATABASE_BACKUP = "database_backup"
JOB_RATING_SYNC = "rating_sync"


class JobAlreadyActive(Exception):
    def __init__(self, dedupe_key: str) -> None:
        super().__init__(f"a job with dedupe key {dedupe_key} is already active")
        self.dedupe_key = dedupe_key


def find_active(session: Session, dedupe_key: str) -> Job | None:
    return session.scalar(
        select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE_STATUSES))
    )


def enqueue(
    session: Session,
    job_type: str,
    dedupe_key: str,
    payload: dict[str, Any] | None = None,
    *,
    not_before: dt.datetime | None = None,
    replace_payload: bool = False,
) -> Job:
    """Queue a job, or update the existing pending one when mutable.

    ``replace_payload`` implements the rating command: one logical command
    per track whose payload carries the latest desired state and revision
    (docs/03 section 6).
    """
    existing = find_active(session, dedupe_key)
    if existing is not None:
        if replace_payload and existing.status == "PENDING":
            existing.payload_json = payload or {}
            existing.not_before = not_before or utcnow()
            existing.updated_at = utcnow()
            session.flush()
            return existing
        raise JobAlreadyActive(dedupe_key)

    job = Job(
        job_id=str(uuid.uuid4()),
        job_type=job_type,
        dedupe_key=dedupe_key,
        status="PENDING",
        not_before=not_before or utcnow(),
        payload_json=payload or {},
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise JobAlreadyActive(dedupe_key) from exc
    return job


def claim_due(session: Session, worker_id: str, now: dt.datetime | None = None) -> Job | None:
    """Claim one due job by taking a lease on it."""
    now = now or utcnow()
    candidate = session.scalar(
        select(Job)
        .where(
            Job.status == "PENDING",
            Job.not_before <= now,
        )
        .order_by(Job.not_before)
        .limit(1)
        .with_for_update(nowait=False)
    )
    if candidate is None:
        return None

    candidate.status = "RUNNING"
    candidate.locked_by = worker_id
    candidate.locked_until = now + LEASE_DURATION
    candidate.attempt += 1
    candidate.updated_at = now
    session.flush()
    return candidate


def release_expired_leases(session: Session, now: dt.datetime | None = None) -> int:
    """After a restart, running jobs whose lease expired become pending again."""
    now = now or utcnow()
    released = 0
    for job in session.scalars(select(Job).where(Job.status == "RUNNING", Job.locked_until < now)):
        job.status = "PENDING"
        job.locked_by = None
        job.locked_until = None
        job.updated_at = now
        released += 1
    return released


def finish(session: Session, job: Job, result: dict[str, Any] | None = None) -> None:
    job.status = "SUCCEEDED"
    job.result_json = result or {}
    job.locked_by = None
    job.locked_until = None
    job.finished_at = utcnow()
    job.updated_at = job.finished_at
    session.flush()


def fail(
    session: Session,
    job: Job,
    error_code: str,
    *,
    retry_in: dt.timedelta | None = None,
) -> None:
    job.last_error_code = error_code
    job.locked_by = None
    job.locked_until = None
    job.updated_at = utcnow()
    if retry_in is not None and job.attempt < job.max_attempts:
        job.status = "PENDING"
        job.not_before = utcnow() + retry_in
    else:
        job.status = "FAILED"
        job.finished_at = utcnow()
    session.flush()
