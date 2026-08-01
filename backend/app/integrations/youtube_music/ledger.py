"""Call ledger, budgets and circuit breaker (docs/03 section 9).

These are application-side safety rails, not an official quota. Every
external call is written to ``api_call_ledger``; automatic jobs are deferred
once a budget is spent, and repeated transient failures open the circuit.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import ApiCallLedger, utcnow

# Automatic per-day limits.
LIBRARY_SYNC_PER_DAY = 4
CANDIDATE_REFRESH_PER_DAY = 1
CANDIDATE_SEED_CALLS_PER_RUN = 10
PUBLISH_WINDOWS_PER_DAY = 1

# Per publish window, per playlist.
MAX_ITEM_CHANGES_PER_WINDOW = 15
MAX_MUTATING_REQUESTS_PER_WINDOW = 15
MAX_PLAYLIST_REQUESTS_PER_WINDOW = 17  # 1 fresh read + 15 mutations + 1 verify
MANAGED_PLAYLIST_COUNT = 3
GLOBAL_PLAYLIST_REQUESTS_PER_DAY = MAX_PLAYLIST_REQUESTS_PER_WINDOW * MANAGED_PLAYLIST_COUNT  # 51
INITIAL_SETUP_REQUESTS_PER_PLAYLIST = 2  # create + verify

# Backoff ladder before the circuit opens.
BACKOFF_SECONDS = (60, 300, 1800)

PLAYLIST_OPERATIONS = frozenset(
    {
        "get_playlist",
        "create_playlist",
        "add_playlist_items",
        "remove_playlist_items",
        "edit_playlist_move",
        "delete_playlist",
    }
)


class BudgetExceeded(Exception):
    """Raised when an automatic job would exceed a local budget."""

    def __init__(self, budget: str, limit: int, used: int) -> None:
        super().__init__(f"{budget} budget exhausted: {used}/{limit}")
        self.budget = budget
        self.limit = limit
        self.used = used


@dataclass(frozen=True, slots=True)
class CircuitState:
    open: bool
    reason: str | None
    opened_at: dt.datetime | None
    retry_after: dt.datetime | None


class SqlCallRecorder:
    """Adapter-facing recorder writing one ledger row per external call."""

    def __init__(
        self,
        session: Session,
        *,
        job_id: str | None = None,
        request_id: str | None = None,
        publication_id: str | None = None,
    ) -> None:
        self._session = session
        self._job_id = job_id
        self._request_id = request_id
        self._publication_id = publication_id

    def record(
        self,
        *,
        operation: str,
        duration_ms: int,
        outcome: str,
        is_mutation: bool,
        playlist_id: str | None = None,
        error_code: str | None = None,
        counts_against_automatic_budget: bool = True,
    ) -> None:
        now = utcnow()
        self._session.add(
            ApiCallLedger(
                provider="ytmusic",
                operation=operation,
                request_fingerprint=operation,
                job_id=self._job_id,
                request_id=self._request_id,
                playlist_id=playlist_id,
                publication_id=self._publication_id,
                is_mutation=is_mutation,
                counts_against_automatic_budget=counts_against_automatic_budget,
                started_at=now - dt.timedelta(milliseconds=duration_ms),
                finished_at=now,
                duration_ms=duration_ms,
                outcome=outcome,
                status_class=error_code,
            )
        )
        self._session.flush()


class CallBudget:
    """Reads the ledger to answer "may this automatic call happen now?"."""

    def __init__(self, session: Session, now: dt.datetime | None = None) -> None:
        self._session = session
        self._now = now or utcnow()

    def _count(
        self,
        *,
        operations: frozenset[str] | tuple[str, ...] | None = None,
        since: dt.datetime | None = None,
        playlist_id: str | None = None,
        only_mutations: bool = False,
        successful_only: bool = True,
    ) -> int:
        query = select(func.count()).select_from(ApiCallLedger)
        query = query.where(ApiCallLedger.counts_against_automatic_budget.is_(True))
        if operations:
            query = query.where(ApiCallLedger.operation.in_(tuple(operations)))
        if since is not None:
            query = query.where(ApiCallLedger.started_at >= since)
        if playlist_id is not None:
            query = query.where(ApiCallLedger.playlist_id == playlist_id)
        if only_mutations:
            query = query.where(ApiCallLedger.is_mutation.is_(True))
        if successful_only:
            query = query.where(ApiCallLedger.outcome == "SUCCESS")
        return self._session.scalar(query) or 0

    @property
    def _day_ago(self) -> dt.datetime:
        return self._now - dt.timedelta(days=1)

    def library_sync_calls_today(self) -> int:
        return self._count(operations=("get_liked_songs",), since=self._day_ago)

    def check_library_sync(self) -> None:
        used = self.library_sync_calls_today()
        if used >= LIBRARY_SYNC_PER_DAY:
            raise BudgetExceeded("library_sync", LIBRARY_SYNC_PER_DAY, used)

    def candidate_refresh_runs_today(self) -> int:
        return self._count(operations=("get_song_related",), since=self._day_ago)

    def playlist_requests_in_window(self, playlist_id: str, window_start: dt.datetime) -> int:
        return self._count(
            operations=PLAYLIST_OPERATIONS, since=window_start, playlist_id=playlist_id
        )

    def mutating_requests_in_window(self, playlist_id: str, window_start: dt.datetime) -> int:
        return self._count(
            operations=PLAYLIST_OPERATIONS,
            since=window_start,
            playlist_id=playlist_id,
            only_mutations=True,
        )

    def global_playlist_requests_today(self) -> int:
        return self._count(operations=PLAYLIST_OPERATIONS, since=self._day_ago)

    def check_publish_window(self, playlist_id: str, window_start: dt.datetime) -> None:
        used = self.playlist_requests_in_window(playlist_id, window_start)
        if used >= MAX_PLAYLIST_REQUESTS_PER_WINDOW:
            raise BudgetExceeded("playlist_requests", MAX_PLAYLIST_REQUESTS_PER_WINDOW, used)
        global_used = self.global_playlist_requests_today()
        if global_used >= GLOBAL_PLAYLIST_REQUESTS_PER_DAY:
            raise BudgetExceeded(
                "global_playlist_requests", GLOBAL_PLAYLIST_REQUESTS_PER_DAY, global_used
            )

    def remaining_requests_in_window(self, playlist_id: str, window_start: dt.datetime) -> int:
        used = self.playlist_requests_in_window(playlist_id, window_start)
        return max(0, MAX_PLAYLIST_REQUESTS_PER_WINDOW - used)

    # -- circuit breaker --------------------------------------------------

    def circuit_state(self) -> CircuitState:
        """Open the circuit after the backoff ladder is exhausted.

        Auth and rate-limit failures count immediately; other transient
        failures need to fill the ladder first.
        """
        # Order by insertion, not by started_at: started_at is derived by
        # subtracting the duration, so a fast success recorded after a slow
        # failure would otherwise sort before it and never close the circuit.
        recent = self._session.scalars(
            select(ApiCallLedger)
            .where(ApiCallLedger.started_at >= self._now - dt.timedelta(hours=1))
            .order_by(ApiCallLedger.id.desc())
            .limit(10)
        ).all()

        failures: list[ApiCallLedger] = []
        for row in recent:
            if row.outcome == "SUCCESS":
                break
            failures.append(row)

        if not failures:
            return CircuitState(open=False, reason=None, opened_at=None, retry_after=None)

        blocking = {"YTM_AUTH_REQUIRED", "YTM_RATE_LIMITED"}
        latest = failures[0]
        if latest.status_class in blocking:
            return CircuitState(
                open=True,
                reason=latest.status_class,
                opened_at=latest.started_at,
                retry_after=None,
            )

        if len(failures) > len(BACKOFF_SECONDS):
            return CircuitState(
                open=True,
                reason="REPEATED_FAILURES",
                opened_at=failures[-1].started_at,
                retry_after=None,
            )

        delay = BACKOFF_SECONDS[len(failures) - 1]
        retry_after = latest.started_at + dt.timedelta(seconds=delay)
        return CircuitState(
            open=retry_after > self._now,
            reason="BACKOFF" if retry_after > self._now else None,
            opened_at=latest.started_at,
            retry_after=retry_after,
        )


def new_request_id() -> str:
    return str(uuid.uuid4())
