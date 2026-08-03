"""Build the desired playlist for one managed kind (docs/05 section 12).

The planner starts from the configured target size and steps down to the
largest size the pool can actually satisfy, so a small library produces a
shorter playlist with an explicit reason rather than a permanent refusal.

The list is *constructed to satisfy the gates*, not sliced off the top of a
wave and hoped for. Taking the first N tracks left the quota, the per-artist
cap and the quality floor entirely to chance, which in practice meant the
gates refused every playlist and the Create button appeared to do nothing.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.publishing.quality_gates import (
    DEFAULT_TARGET_SIZE,
    MAX_TRACKS_PER_ARTIST,
    MIN_ARTIST_RATIO,
    MIN_PUBLISH_SIZE,
    QUALITY_GATE_VERSION,
    GateCandidate,
    evaluate,
    largest_feasible_size,
)
from app.recommender.rules import QUALITY_EXPECTED_FLOOR
from app.recommender.temperature import familiar_quota
from app.recommender.wave import WaveItem, WaveRequest, generate_wave

PLAYLIST_TEMPERATURES = {"FAMILIAR": 20, "BALANCE": 50, "DISCOVERY": 80}

# Tried in order: three tracks per artist is the gate maximum, but a narrow
# library may need a stricter cap to reach the distinct-artist ratio.
ARTIST_CAPS = (MAX_TRACKS_PER_ARTIST, 2, 1)

# How much material to rank before choosing, as a multiple of the target size.
POOL_MULTIPLIER = 6

# Each pick is sampled from this many leading candidates.
SELECTION_WINDOW = 20


def _artist_of(item: WaveItem) -> str | None:
    return item.artists[0] if item.artists else None


def _take(
    items: Sequence[WaveItem],
    wanted: int,
    cap: int,
    used: dict[str, int],
    rng: random.Random | None = None,
) -> list[WaveItem] | None:
    """Strong tracks that keep every artist under the cap.

    Sampled from the leading candidates rather than taken strictly in order:
    always draining the top of the list made Regenerate return nearly the same
    sixty tracks even with two hundred eligible ones to choose from.
    """
    chosen: list[WaveItem] = []
    remaining = list(items)
    while remaining and len(chosen) < wanted:
        window = remaining[:SELECTION_WINDOW] if rng is not None else remaining[:1]
        admissible = [
            (position, item)
            for position, item in enumerate(window)
            if _artist_of(item) is None or used.get(_artist_of(item) or "", 0) < cap
        ]
        if not admissible:
            del remaining[: len(window)]
            continue
        position, item = admissible[rng.randrange(len(admissible))] if rng else admissible[0]
        remaining.pop(position)
        artist = _artist_of(item)
        if artist is not None:
            used[artist] = used.get(artist, 0) + 1
        chosen.append(item)
    return chosen if len(chosen) == wanted else None


def _arrange(chosen: list[WaveItem]) -> list[WaveItem]:
    """Order so no two neighbours share an artist, familiar interleaved."""
    remaining = list(chosen)
    ordered: list[WaveItem] = []
    previous: str | None = None
    want_familiar = chosen and chosen[0].familiarity == "FAMILIAR"
    while remaining:
        index = next(
            (
                position
                for position, item in enumerate(remaining)
                if _artist_of(item) != previous
                and (item.familiarity == "FAMILIAR") == want_familiar
            ),
            None,
        )
        if index is None:
            index = next(
                (
                    position
                    for position, item in enumerate(remaining)
                    if _artist_of(item) != previous
                ),
                0,
            )
        item = remaining.pop(index)
        ordered.append(item)
        previous = _artist_of(item)
        want_familiar = item.familiarity != "FAMILIAR"
    return ordered


def _compose(
    items: Sequence[WaveItem],
    size: int,
    target_percent: int,
    cap: int,
    rng: random.Random | None = None,
) -> list[WaveItem] | None:
    """A list of exactly ``size`` tracks that should satisfy every gate."""
    familiar_wanted = round(size * target_percent / 100)
    discovery_wanted = size - familiar_wanted

    used: dict[str, int] = {}
    familiar = _take(
        [item for item in items if item.familiarity == "FAMILIAR"], familiar_wanted, cap, used, rng
    )
    if familiar is None:
        return None
    discovery = _take(
        [item for item in items if item.familiarity != "FAMILIAR"],
        discovery_wanted,
        cap,
        used,
        rng,
    )
    if discovery is None:
        return None

    chosen = [*familiar, *discovery]
    artists = {_artist_of(item) for item in chosen if _artist_of(item)}
    if len(artists) < math.ceil(MIN_ARTIST_RATIO * size):
        return None
    return _arrange(chosen)


@dataclass(frozen=True, slots=True)
class PreviewTrack:
    """One row of the reviewable list."""

    video_id: str
    title: str
    artists: tuple[str, ...]
    familiarity: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesiredListResult:
    kind: str
    passed: bool
    video_ids: tuple[str, ...]
    configured_target_size: int
    effective_target_size: int
    minimum_publish_size: int
    required_familiar: int
    available_familiar: int
    required_discovery: int
    available_discovery: int
    failures: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    gate_version: str = QUALITY_GATE_VERSION
    tracks: tuple[PreviewTrack, ...] = ()
    random_seed: int | None = None


def build_desired_list(
    db: Session,
    kind: str,
    *,
    configured_target_size: int = DEFAULT_TARGET_SIZE,
    comparable_remote_mean: float | None = None,
    score_policy_changed: bool = False,
    random_seed: int | None = None,
) -> DesiredListResult:
    temperature = PLAYLIST_TEMPERATURES[kind]
    target_percent = familiar_quota(temperature)

    # A fixed seed per kind made Preview reproducible but also made it the only
    # answer there ever was — asking again returned the same list. The caller
    # now supplies a seed, so Regenerate can offer a genuinely different
    # candidate while the reviewed one stays reproducible.
    seed = random_seed if random_seed is not None else hash(kind) % (2**31)
    rng = random.Random(seed)
    pool = generate_wave(
        db,
        WaveRequest(
            temperature=temperature,
            mood="ANY",
            # A generous pool: at three times the target the same handful of
            # top-scoring tracks came back on every Regenerate.
            length=configured_target_size * POOL_MULTIPLIER,
            random_seed=seed,
            for_publishing=True,
        ),
    )
    # Only tracks above the shared quality floor may enter a playlist, so the
    # availability counts have to be taken after that filter, not before.
    eligible = [
        item for item in pool.items if item.quality_expected >= QUALITY_EXPECTED_FLOOR
    ]
    available_familiar = sum(1 for item in eligible if item.familiarity == "FAMILIAR")
    available_discovery = sum(1 for item in eligible if item.familiarity == "DISCOVERY")

    size = largest_feasible_size(
        configured_target_size=configured_target_size,
        target_familiar_percent=target_percent,
        available_familiar=available_familiar,
        available_discovery=available_discovery,
    )
    if size is None:
        return DesiredListResult(
            kind=kind,
            passed=False,
            video_ids=(),
            configured_target_size=configured_target_size,
            effective_target_size=0,
            minimum_publish_size=MIN_PUBLISH_SIZE,
            required_familiar=max(0, round(MIN_PUBLISH_SIZE * max(0, target_percent - 10) / 100)),
            available_familiar=available_familiar,
            required_discovery=max(
                0, MIN_PUBLISH_SIZE - round(MIN_PUBLISH_SIZE * (target_percent + 10) / 100)
            ),
            available_discovery=available_discovery,
            failures=("INSUFFICIENT_POOL",),
        )

    # Step down from the largest feasible size, loosening the per-artist cap
    # only as far as the pool forces. The first combination that satisfies the
    # gates wins; if none does, the last outcome carries the reason.
    selected: list[WaveItem] = []
    outcome = None
    for cap in ARTIST_CAPS:
        for attempt in range(size, MIN_PUBLISH_SIZE - 1, -1):
            composed = _compose(eligible, attempt, target_percent, cap, rng)
            if composed is None:
                continue
            candidate_outcome = evaluate(
                [
                    GateCandidate(
                        video_id=item.video_id,
                        artist_id=_artist_of(item),
                        familiar=item.familiarity == "FAMILIAR",
                        quality_expected=item.quality_expected,
                        seed_video_id=None,
                    )
                    for item in composed
                ],
                configured_target_size=configured_target_size,
                target_familiar_percent=target_percent,
                comparable_remote_mean=comparable_remote_mean,
                score_policy_changed=score_policy_changed,
            )
            if outcome is None or candidate_outcome.passed:
                outcome = candidate_outcome
                selected = composed
            if candidate_outcome.passed:
                break
        if outcome is not None and outcome.passed:
            break

    if outcome is None:
        outcome = evaluate(
            [],
            configured_target_size=configured_target_size,
            target_familiar_percent=target_percent,
            comparable_remote_mean=comparable_remote_mean,
            score_policy_changed=score_policy_changed,
        )

    tracks = tuple(
        PreviewTrack(
            video_id=item.video_id,
            title=item.title,
            artists=tuple(item.artists),
            familiarity=item.familiarity,
            reason_codes=tuple(item.reason_codes),
        )
        for item in selected
    )

    return DesiredListResult(
        kind=kind,
        passed=outcome.passed,
        video_ids=tuple(item.video_id for item in selected) if outcome.passed else (),
        tracks=tracks if outcome.passed else (),
        random_seed=seed,
        configured_target_size=configured_target_size,
        effective_target_size=outcome.effective_target_size,
        minimum_publish_size=MIN_PUBLISH_SIZE,
        required_familiar=outcome.required_familiar,
        available_familiar=available_familiar,
        required_discovery=outcome.required_discovery,
        available_discovery=available_discovery,
        failures=outcome.failures,
        skipped=outcome.skipped,
        reasons=outcome.reasons,
    )
