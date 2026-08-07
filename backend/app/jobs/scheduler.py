"""In-process scheduler started from the FastAPI lifespan (docs/02 section 8).

A single Uvicorn worker polls for due jobs; every run takes a lease, so a
restart resumes safely and a stray second process cannot double-publish.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket

from app.integrations.youtube_music.adapter import YouTubeMusicAdapter
from app.integrations.youtube_music.ledger import CallBudget, SqlCallRecorder
from app.jobs import queue, worker
from app.jobs.periodic import enqueue_due_jobs
from app.persistence.database import session_scope
from app.settings import Settings

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0

# Draining a backlog without sleeping is fine, spinning is not: after this
# many consecutive runs the loop takes its normal pause regardless.
MAX_CONSECUTIVE_RUNS = 20

# Only these reach YouTube. Training, cleanup, backup and the affinity
# rollup are entirely local, so an open circuit is no reason to defer them —
# doing so used to turn every poll into a failed job row.
EXTERNAL_JOB_TYPES = frozenset(
    {
        queue.JOB_LIBRARY_SYNC,
        queue.JOB_CANDIDATE_REFRESH,
        queue.JOB_GRAPH_EXPAND,
        queue.JOB_PLAYLIST_PUBLISH,
        queue.JOB_RATING_SYNC,
    }
)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_once(settings: Settings, catalog_factory=None) -> bool:
    """Claim and run at most one due job. Returns True if a job ran."""
    with session_scope() as session:
        queue.release_expired_leases(session)
        enqueue_due_jobs(session)
        job = queue.claim_due(session, worker_id())
        if job is None:
            return False

        circuit = CallBudget(session).circuit_state()
        if circuit.open and job.job_type in EXTERNAL_JOB_TYPES:
            queue.fail(session, job, f"CIRCUIT_OPEN:{circuit.reason}")
            logger.info(
                "job deferred, circuit open",
                extra={"operation": job.job_type, "job_id": job.job_id},
            )
            return True

        recorder = SqlCallRecorder(session, job_id=job.job_id)
        catalog = (
            catalog_factory(recorder)
            if catalog_factory is not None
            else YouTubeMusicAdapter(settings, recorder=recorder)
        )
        logger.info("job started", extra={"operation": job.job_type, "job_id": job.job_id})
        worker.execute(session, job, catalog)
        logger.info(
            "job finished",
            extra={"operation": job.job_type, "job_id": job.job_id, "outcome": job.status},
        )
        return True


async def scheduler_loop(settings: Settings, catalog_factory=None) -> None:
    consecutive = 0
    while True:
        try:
            ran = await asyncio.to_thread(run_once, settings, catalog_factory)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any job failure
            logger.exception("scheduler iteration failed")
            ran = False

        consecutive = consecutive + 1 if ran else 0
        if consecutive >= MAX_CONSECUTIVE_RUNS:
            logger.warning(
                "scheduler ran %d jobs back to back, pausing",
                consecutive,
                extra={"operation": "scheduler"},
            )
            consecutive = 0
            ran = False
        await asyncio.sleep(0 if ran else POLL_INTERVAL_SECONDS)


class Scheduler:
    def __init__(self, settings: Settings, catalog_factory=None) -> None:
        self._settings = settings
        self._catalog_factory = catalog_factory
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                scheduler_loop(self._settings, self._catalog_factory), name="tuner-scheduler"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
