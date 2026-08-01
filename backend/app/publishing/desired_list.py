"""Build the desired playlist for one managed kind (docs/05 section 12).

The planner starts from the configured target size and steps down to the
largest size the pool can actually satisfy, so a small library produces a
shorter playlist with an explicit reason rather than a permanent refusal.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.publishing.quality_gates import (
    DEFAULT_TARGET_SIZE,
    MIN_PUBLISH_SIZE,
    QUALITY_GATE_VERSION,
    GateCandidate,
    evaluate,
    largest_feasible_size,
)
from app.recommender.temperature import familiar_quota
from app.recommender.wave import WaveRequest, generate_wave

PLAYLIST_TEMPERATURES = {"FAMILIAR": 20, "BALANCE": 50, "DISCOVERY": 80}


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


def build_desired_list(
    db: Session,
    kind: str,
    *,
    configured_target_size: int = DEFAULT_TARGET_SIZE,
    comparable_remote_mean: float | None = None,
    score_policy_changed: bool = False,
) -> DesiredListResult:
    temperature = PLAYLIST_TEMPERATURES[kind]
    target_percent = familiar_quota(temperature)

    # Generate a generous pool once, then choose the largest feasible size.
    pool = generate_wave(
        db,
        WaveRequest(
            temperature=temperature,
            mood="ANY",
            length=configured_target_size * 3,
            random_seed=hash(kind) % (2**31),
        ),
    )
    available_familiar = sum(1 for item in pool.items if item.familiarity == "FAMILIAR")
    available_discovery = sum(1 for item in pool.items if item.familiarity == "DISCOVERY")

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

    selected = list(pool.items[:size])
    candidates = [
        GateCandidate(
            video_id=item.video_id,
            artist_id=item.artists[0] if item.artists else None,
            familiar=item.familiarity == "FAMILIAR",
            quality_expected=item.quality_expected,
            seed_video_id=None,
        )
        for item in selected
    ]

    outcome = evaluate(
        candidates,
        configured_target_size=configured_target_size,
        target_familiar_percent=target_percent,
        comparable_remote_mean=comparable_remote_mean,
        score_policy_changed=score_policy_changed,
    )

    return DesiredListResult(
        kind=kind,
        passed=outcome.passed,
        video_ids=tuple(item.video_id for item in selected) if outcome.passed else (),
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
