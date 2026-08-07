"""Turn raw telemetry events into a playback session summary (docs/04 s.4-7).

Key rules encoded here:

* ``played_seconds`` comes from the client's verified PLAYING intervals; the
  server only caps implausible values, it never derives listening from
  ``positionSeconds``.
* A skip exists only after an explicit next; pause, buffering, player error,
  hidden and page close are neutral.
* Without a duration the session falls back to absolute-time rules and is
  marked ``classification_basis=ABSOLUTE_TIME``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app.player.reward import REWARD_VERSION, RewardInputs, compute_reward

AGGREGATION_VERSION = "aggregation-v1"

QUALIFYING_SECONDS = 10.0
POSITION_CAP_MARGIN = 5.0

# Ratio thresholds.
EARLY_SKIP_RATIO = 0.20
MID_SKIP_RATIO = 0.60
PARTIAL_LISTEN_RATIO = 0.60
COMPLETION_RATIO = 0.90
ENDED_COMPLETION_RATIO = 0.70
LARGE_SEEK_MIN_SECONDS = 30.0
LARGE_SEEK_RATIO = 0.20

# Absolute-time fallbacks when the duration is unknown.
ABSOLUTE_EARLY_SKIP_SECONDS = 30.0
ABSOLUTE_MID_SKIP_SECONDS = 120.0
ABSOLUTE_COMPLETION_SECONDS = 60.0

EXPLICIT_NEXT_EVENTS = frozenset({"next_clicked"})
NEUTRAL_EVENTS = frozenset(
    {
        "paused",
        "buffering_started",
        "player_error",
        "visibility_changed",
        "page_closing",
        "track_cued",
        "play_resumed",
    }
)


@dataclass(slots=True)
class SessionAccumulator:
    """Folded view of one session's events."""

    session_id: str
    video_id: str
    started_at: dt.datetime | None = None
    last_event_at: dt.datetime | None = None
    played_seconds: float = 0.0
    max_position_seconds: float = 0.0
    seek_forward_seconds: float = 0.0
    seek_backward_seconds: float = 0.0
    seek_forward_count: int = 0
    seek_backward_count: int = 0
    buffered_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    effective_duration_seconds: float | None = None
    duration_source: str = "UNKNOWN"
    explicit_next: bool = False
    ended: bool = False
    replayed: bool = False
    explicit_rating: str | None = None
    queue_id: str | None = None
    generation_id: str | None = None
    termination_reason: str | None = None
    event_types: list[str] = field(default_factory=list)


def _number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _apply_duration(acc: SessionAccumulator, payload: dict[str, Any]) -> None:
    """PLAYER duration always wins over catalogue metadata (docs/04 s.4)."""
    source = payload.get("durationSource")
    duration = _number(payload, "effectiveDurationSeconds")
    if duration is None or duration <= 0:
        return
    if source == "PLAYER":
        acc.effective_duration_seconds = duration
        acc.duration_source = "PLAYER"
    elif acc.duration_source != "PLAYER":
        acc.effective_duration_seconds = duration
        acc.duration_source = "METADATA" if source == "METADATA" else acc.duration_source
        if acc.duration_source == "UNKNOWN":
            acc.duration_source = "METADATA"


def fold_event(acc: SessionAccumulator, event: dict[str, Any]) -> SessionAccumulator:
    """Apply one event. Cumulative counters take the maximum seen value."""
    event_type = str(event.get("type", ""))
    payload = event.get("payload") or {}
    occurred_at = event.get("occurred_at")

    acc.event_types.append(event_type)
    if isinstance(occurred_at, dt.datetime):
        if acc.started_at is None or occurred_at < acc.started_at:
            acc.started_at = occurred_at
        if acc.last_event_at is None or occurred_at > acc.last_event_at:
            acc.last_event_at = occurred_at

    if isinstance(payload, dict):
        _apply_duration(acc, payload)
        # Cumulative counters: a replayed batch must not double count.
        for key, attribute in (
            ("playedSeconds", "played_seconds"),
            ("positionSeconds", "max_position_seconds"),
            ("seekForwardSeconds", "seek_forward_seconds"),
            ("seekBackwardSeconds", "seek_backward_seconds"),
            ("bufferedSeconds", "buffered_seconds"),
            ("wallClockSeconds", "wall_clock_seconds"),
        ):
            value = _number(payload, key)
            if value is not None:
                setattr(acc, attribute, max(getattr(acc, attribute), value))
        for key, attribute in (
            ("seekForwardCount", "seek_forward_count"),
            ("seekBackwardCount", "seek_backward_count"),
        ):
            value = _number(payload, key)
            if value is not None:
                setattr(acc, attribute, max(getattr(acc, attribute), int(value)))
        for key, attribute in (("queueId", "queue_id"), ("generationId", "generation_id")):
            value = payload.get(key)
            if isinstance(value, str) and value:
                setattr(acc, attribute, value)

    if event_type in EXPLICIT_NEXT_EVENTS:
        acc.explicit_next = True
        acc.termination_reason = "explicit_next"
    elif event_type == "ended":
        acc.ended = True
        acc.termination_reason = "ended"
    elif event_type == "replay_started":
        acc.replayed = True
    elif event_type == "like_set":
        acc.explicit_rating = "LIKE"
    elif event_type == "dislike_set":
        acc.explicit_rating = "DISLIKE"
    elif event_type == "veto_set":
        # "Don't Like At All" is a dislike-grade signal for learning; the
        # pool exclusion itself lives in taste_vetoes (docs/05 s.11).
        acc.explicit_rating = "DISLIKE"
    elif event_type == "seek_backward":
        acc.seek_backward_count = max(acc.seek_backward_count, 1)
    elif event_type == "seek_forward":
        acc.seek_forward_count = max(acc.seek_forward_count, 1)
    elif event_type == "player_error" and acc.termination_reason is None:
        # Recorded so the track lands in the 24h playback-error cooldown
        # (docs/05 section 4) — never as a skip.
        acc.termination_reason = "player_error"
    elif event_type == "page_closing" and acc.termination_reason is None:
        acc.termination_reason = "abandoned_unknown"

    return acc


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: str
    video_id: str
    effective_duration_seconds: float | None
    duration_source: str
    played_seconds: float
    played_ratio: float | None
    classification_basis: str
    early_skip: bool
    mid_skip: bool
    completed: bool
    ended_unqualified: bool
    large_forward_seek: bool
    replayed: bool
    explicit_rating: str | None
    qualified: bool
    reward: float | None
    reward_version: str
    termination_reason: str | None


def _cap_played_seconds(acc: SessionAccumulator) -> float:
    """Never trust a played time above what the clock allows."""
    played = max(0.0, acc.played_seconds)
    if acc.effective_duration_seconds is not None:
        played = min(played, acc.effective_duration_seconds + POSITION_CAP_MARGIN)
    elif acc.wall_clock_seconds > 0:
        played = min(played, acc.wall_clock_seconds + POSITION_CAP_MARGIN)
    return played


def summarize(acc: SessionAccumulator) -> SessionSummary:
    played = _cap_played_seconds(acc)
    duration = acc.effective_duration_seconds
    ratio_basis = duration is not None and duration > 0

    early_skip = mid_skip = completed = ended_unqualified = False
    partial_listen = False
    played_ratio: float | None = None

    if ratio_basis:
        assert duration is not None
        played_ratio = min(played / duration, 1.0)
        large_seek = acc.seek_forward_seconds >= max(
            LARGE_SEEK_MIN_SECONDS, LARGE_SEEK_RATIO * duration
        )
        if acc.explicit_next:
            early_skip = played_ratio < EARLY_SKIP_RATIO
            mid_skip = EARLY_SKIP_RATIO <= played_ratio < MID_SKIP_RATIO
        completed = played_ratio >= COMPLETION_RATIO or (
            acc.ended and played_ratio >= ENDED_COMPLETION_RATIO and not large_seek
        )
        partial_listen = (
            not completed
            and not acc.explicit_next
            and PARTIAL_LISTEN_RATIO <= played_ratio < COMPLETION_RATIO
        )
        basis = "RATIO"
    else:
        large_seek = acc.seek_forward_seconds >= LARGE_SEEK_MIN_SECONDS
        if acc.explicit_next:
            early_skip = played < ABSOLUTE_EARLY_SKIP_SECONDS
            mid_skip = ABSOLUTE_EARLY_SKIP_SECONDS <= played < ABSOLUTE_MID_SKIP_SECONDS
        if acc.ended:
            if played >= ABSOLUTE_COMPLETION_SECONDS and not large_seek:
                completed = True
            else:
                ended_unqualified = True
        basis = "ABSOLUTE_TIME"

    # An explicit Next qualifies the session even in the first seconds, so a
    # deliberate early skip is never lost (docs/04 section 5, BR-006).
    qualified = played >= QUALIFYING_SECONDS or acc.explicit_rating is not None or acc.explicit_next

    reward = None
    if qualified:
        reward = compute_reward(
            RewardInputs(
                explicit_rating=acc.explicit_rating,
                completed=completed,
                partial_listen=partial_listen,
                replayed=acc.replayed,
                seek_backward_count=acc.seek_backward_count,
                early_skip=early_skip,
                mid_skip=mid_skip,
                absolute_basis=not ratio_basis,
            )
        )

    return SessionSummary(
        session_id=acc.session_id,
        video_id=acc.video_id,
        effective_duration_seconds=duration,
        duration_source=acc.duration_source,
        played_seconds=played,
        played_ratio=played_ratio,
        classification_basis=basis,
        early_skip=early_skip,
        mid_skip=mid_skip,
        completed=completed,
        ended_unqualified=ended_unqualified,
        large_forward_seek=large_seek,
        replayed=acc.replayed,
        explicit_rating=acc.explicit_rating,
        qualified=qualified,
        reward=reward,
        reward_version=REWARD_VERSION,
        termination_reason=acc.termination_reason,
    )
