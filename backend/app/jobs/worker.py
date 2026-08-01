"""Job execution: one handler per job type."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.domain.catalog import Rating
from app.integrations.youtube_music.errors import AuthError, IntegrationError, RateLimited
from app.integrations.youtube_music.ledger import BudgetExceeded
from app.integrations.youtube_music.port import MusicCatalogPort
from app.jobs import queue
from app.jobs.candidate_refresh import run_candidate_refresh
from app.jobs.library_sync import SyncCooldownActive, run_library_sync
from app.persistence import repositories as repo
from app.persistence.models import Job, utcnow

logger = logging.getLogger(__name__)

RETRY_LADDER = (
    dt.timedelta(seconds=60),
    dt.timedelta(minutes=5),
    dt.timedelta(minutes=30),
)


def retry_delay(attempt: int) -> dt.timedelta | None:
    """60s -> 5min -> 30min, then stop retrying (docs/03 section 9)."""
    index = max(0, attempt - 1)
    if index >= len(RETRY_LADDER):
        return None
    return RETRY_LADDER[index]


def handle_library_sync(session: Session, job: Job, catalog: MusicCatalogPort) -> dict[str, object]:
    run = run_library_sync(session, catalog, force=bool(job.payload_json.get("force")))
    return {
        "syncRunId": run.sync_run_id,
        "liked": run.liked_count,
        "playlists": run.playlist_count,
        "history": run.history_count,
    }


def handle_rating_sync(session: Session, job: Job, catalog: MusicCatalogPort) -> dict[str, object]:
    """Push the newest desired rating; confirm only the revision we sent."""
    video_id = str(job.payload_json.get("videoId", ""))
    if not video_id:
        raise ValueError("rating job payload is missing videoId")

    state = repo.library_state(session, video_id)
    sent_revision = state.rating_revision
    desired = Rating(state.desired_rating or "INDIFFERENT")

    catalog.rate_track(video_id, desired)

    session.refresh(state)
    if state.rating_revision == sent_revision:
        state.rating_sync_status = "SYNCED"
        state.rating_synced_revision = sent_revision
        state.remote_rating_synced_at = utcnow()
        session.flush()
        return {"videoId": video_id, "revision": sent_revision, "confirmed": True}

    # The user changed their mind while the call was in flight: do not confirm,
    # queue a successor with the newest payload instead.
    logger.info(
        "rating superseded while in flight",
        extra={"operation": "rating_sync", "video_id": video_id},
    )
    return {"videoId": video_id, "revision": sent_revision, "confirmed": False, "superseded": True}


def handle_candidate_refresh(
    session: Session, job: Job, catalog: MusicCatalogPort
) -> dict[str, object]:
    seed = job.payload_json.get("randomSeed")
    result = run_candidate_refresh(
        session, catalog, random_seed=int(seed) if isinstance(seed, int) else None
    )
    return {
        "seeds": list(result.seeds),
        "edgesWritten": result.edges_written,
        "callsMade": result.calls_made,
    }


Handler = Callable[[Session, Job, MusicCatalogPort], dict[str, object]]

HANDLERS: dict[str, Handler] = {
    queue.JOB_LIBRARY_SYNC: handle_library_sync,
    queue.JOB_RATING_SYNC: handle_rating_sync,
    queue.JOB_CANDIDATE_REFRESH: handle_candidate_refresh,
}


def execute(session: Session, job: Job, catalog: MusicCatalogPort) -> None:
    handler = HANDLERS.get(job.job_type)
    if handler is None:
        queue.fail(session, job, "UNKNOWN_JOB_TYPE")
        return

    try:
        result = handler(session, job, catalog)
    except (AuthError, BudgetExceeded, SyncCooldownActive) as exc:
        # Not retryable without operator action or a new time window.
        queue.fail(session, job, getattr(exc, "code", type(exc).__name__))
        return
    except RateLimited as exc:
        queue.fail(session, job, exc.code, retry_in=dt.timedelta(minutes=30))
        return
    except IntegrationError as exc:
        queue.fail(session, job, exc.code, retry_in=retry_delay(job.attempt))
        return
    except Exception as exc:  # noqa: BLE001 - a bad job must not kill the loop
        logger.exception("job crashed", extra={"operation": job.job_type, "job_id": job.job_id})
        queue.fail(session, job, type(exc).__name__, retry_in=retry_delay(job.attempt))
        return

    queue.finish(session, job, result)

    if result.get("superseded"):
        payload = dict(job.payload_json)
        queue.enqueue(
            session,
            job.job_type,
            job.dedupe_key,
            payload,
            not_before=utcnow() + dt.timedelta(seconds=2),
            replace_payload=True,
        )
