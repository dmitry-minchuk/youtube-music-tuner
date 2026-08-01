"""Wave queue generation (docs/05 sections 4-11, docs/11 section 2)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.persistence.models import (
    Artist,
    CandidateEdge,
    LibraryTrackState,
    PlaybackSession,
    Track,
    TrackArtist,
    utcnow,
)
from app.recommender.rules import (
    RuleFeatures,
    passes_quality_floor,
    rule_score,
    rule_to_quality_expected,
)
from app.recommender.temperature import discovery_quota, exploration_alpha, familiar_quota
from app.recommender.wave import WaveRequest, generate_wave


def seed_track(
    db: Session,
    video_id: str,
    *,
    artist: str = "Alpha",
    liked: bool = False,
    disliked: bool = False,
    album: str | None = None,
) -> None:
    db.add(Track(video_id=video_id, title=f"Track {video_id}", album_id=album, is_playable=True))
    artist_id = f"UC{artist}"
    if db.get(Artist, artist_id) is None:
        db.add(Artist(artist_id=artist_id, name=artist))
    db.add(TrackArtist(track_id=video_id, artist_id=artist_id, ordinal=0))
    if liked or disliked:
        db.add(LibraryTrackState(video_id=video_id, is_liked=liked, is_disliked=disliked))
    db.flush()


def seed_edge(db: Session, seed: str, candidate: str, source: str = "RADIO", rank: int = 1) -> None:
    db.add(
        CandidateEdge(
            seed_video_id=seed,
            candidate_video_id=candidate,
            source_type=source,
            source_key=seed,
            rank=rank,
            fetched_at=utcnow(),
            expires_at=utcnow() + dt.timedelta(days=7),
        )
    )
    db.flush()


def build_pool(db: Session, liked_count: int = 6, discovery_count: int = 20) -> None:
    for index in range(liked_count):
        seed_track(db, f"liked-{index}", artist=f"Fav{index}", liked=True)
    for index in range(discovery_count):
        seed_track(db, f"disc-{index}", artist=f"New{index}")
        seed_edge(db, "liked-0", f"disc-{index}", rank=index + 1)


# -- temperature ----------------------------------------------------------


def test_discovery_quota_increases_monotonically() -> None:
    quotas = [discovery_quota(value) for value in (10, 40, 70, 95)]
    assert quotas == sorted(quotas)
    assert quotas == [20, 45, 70, 85]


def test_familiar_and_discovery_quotas_are_complementary() -> None:
    for temperature in (0, 25, 26, 60, 61, 85, 86, 100):
        assert familiar_quota(temperature) + discovery_quota(temperature) == 100


def test_exploration_alpha_grows_with_temperature() -> None:
    alphas = [exploration_alpha(value) for value in (0, 30, 70, 100)]
    assert alphas == sorted(alphas)
    assert alphas[0] >= 0.10
    assert alphas[-1] <= 1.30


# -- rule score and the shared quality scale ------------------------------


def test_rule_score_is_clamped_to_unit_range() -> None:
    assert rule_score(RuleFeatures(fatigue_penalty=5.0)) == 0.0
    assert (
        rule_score(
            RuleFeatures(
                source_strength=1.0,
                seed_affinity=1.0,
                artist_affinity=1.0,
                rediscovery=1.0,
                novelty=1.0,
            )
        )
        == 1.0
    )


def test_quality_scale_maps_rule_score_onto_minus_one_to_one() -> None:
    assert rule_to_quality_expected(0.0) == -1.0
    assert rule_to_quality_expected(0.5) == 0.0
    assert rule_to_quality_expected(1.0) == 1.0


def test_quality_floor_is_exactly_rule_score_zero_point_four() -> None:
    """quality_expected >= -0.20 means rule_score >= 0.40 (docs/05 s.7)."""
    assert passes_quality_floor(rule_to_quality_expected(0.40)) is True
    assert passes_quality_floor(rule_to_quality_expected(0.39)) is False


# -- hard filters ---------------------------------------------------------


def test_disliked_track_never_appears_at_any_temperature(db_session) -> None:
    build_pool(db_session)
    seed_track(db_session, "hated", artist="Bad", disliked=True)
    seed_edge(db_session, "liked-0", "hated")

    for temperature in (0, 25, 50, 75, 100):
        result = generate_wave(db_session, WaveRequest(temperature=temperature, length=30))
        assert all(item.video_id != "hated" for item in result.items)


def test_unplayable_track_is_excluded(db_session) -> None:
    build_pool(db_session)
    db_session.add(Track(video_id="gone", title="Gone", is_playable=False))
    db_session.flush()
    seed_edge(db_session, "liked-0", "gone")

    result = generate_wave(db_session, WaveRequest(length=30))
    assert all(item.video_id != "gone" for item in result.items)


def test_recently_played_tracks_are_excluded(db_session) -> None:
    build_pool(db_session)
    db_session.add(PlaybackSession(session_id="s1", video_id="disc-0", started_at=utcnow()))
    db_session.flush()

    result = generate_wave(db_session, WaveRequest(length=30))
    assert all(item.video_id != "disc-0" for item in result.items)


def test_playback_error_puts_a_track_on_cooldown(db_session) -> None:
    build_pool(db_session)
    db_session.add(
        PlaybackSession(
            session_id="s-err",
            video_id="disc-1",
            started_at=utcnow() - dt.timedelta(hours=2),
            termination_reason="player_error",
        )
    )
    db_session.flush()

    result = generate_wave(db_session, WaveRequest(length=30))
    assert all(item.video_id != "disc-1" for item in result.items)


def test_explicitly_excluded_candidates_are_dropped(db_session) -> None:
    build_pool(db_session)
    result = generate_wave(
        db_session, WaveRequest(length=30, exclude_video_ids=frozenset({"disc-2"}))
    )
    assert all(item.video_id != "disc-2" for item in result.items)


# -- reproducibility and diversity ----------------------------------------


def test_a_fixed_seed_reproduces_the_queue(db_session) -> None:
    """Same state plus same seed means same queue.

    A generation changes the state — the wave before this one is part of the
    input now — so the first one is rolled back before the queue is rebuilt.
    """
    from app.persistence.models import QueueGeneration, QueueItem

    build_pool(db_session)
    first = generate_wave(db_session, WaveRequest(length=20, random_seed=1234))

    db_session.query(QueueItem).filter_by(generation_id=first.generation_id).delete()
    db_session.query(QueueGeneration).filter_by(generation_id=first.generation_id).delete()
    db_session.flush()

    second = generate_wave(db_session, WaveRequest(length=20, random_seed=1234))
    assert [item.video_id for item in first.items] == [item.video_id for item in second.items]


def test_consecutive_waves_do_not_repeat_each_other(db_session) -> None:
    """The freshness goal: restarting the wave must feel like a new wave."""
    build_pool(db_session, liked_count=10, discovery_count=90)

    first = generate_wave(db_session, WaveRequest(length=40, random_seed=1))
    second = generate_wave(db_session, WaveRequest(length=40, random_seed=2))

    overlap = {item.video_id for item in first.items} & {item.video_id for item in second.items}
    assert len(overlap) / len(second.items) <= 0.30
    assert second.overlap_previous_percent <= 30


def test_the_same_seed_still_yields_a_different_wave_next_time(db_session) -> None:
    """Even an unchanged pool and an unchanged seed must not repeat a wave."""
    build_pool(db_session, liked_count=10, discovery_count=90)

    first = generate_wave(db_session, WaveRequest(length=40, random_seed=99))
    second = generate_wave(db_session, WaveRequest(length=40, random_seed=99))

    assert [item.video_id for item in first.items] != [item.video_id for item in second.items]


def test_sampling_alone_varies_the_order(db_session) -> None:
    """Stochastic selection, not just exclusion, drives the variation."""
    import random as _random

    from app.recommender.reranker import RerankCandidate, rerank

    candidates = [
        RerankCandidate(
            video_id=f"v{index}",
            score=1.0 - index * 0.005,
            artist_id=f"a{index}",
            album_id=None,
            seed_video_id="seed",
            source_type="RADIO",
            familiar=False,
            quality_expected=0.5,
            proven_playable=True,
        )
        for index in range(60)
    ]
    first = [item.video_id for item in rerank(candidates, 20, 0, rng=_random.Random(1)).items]
    second = [item.video_id for item in rerank(candidates, 20, 0, rng=_random.Random(2)).items]
    assert first != second
    assert [item.video_id for item in rerank(candidates, 20, 0).items] == [
        item.video_id for item in rerank(candidates, 20, 0).items
    ]


def test_one_artist_cannot_dominate_a_window_of_five(db_session) -> None:
    for index in range(12):
        seed_track(db_session, f"same-{index}", artist="Monopolist", liked=True)
    for index in range(12):
        seed_track(db_session, f"other-{index}", artist=f"Other{index}", liked=True)

    result = generate_wave(db_session, WaveRequest(length=20, random_seed=7))
    artists_by_position = []
    for item in result.items:
        artists_by_position.append("Monopolist" if item.video_id.startswith("same-") else "other")

    for start in range(max(0, len(artists_by_position) - 4)):
        window = artists_by_position[start : start + 5]
        assert window.count("Monopolist") <= 1


def test_temperature_shifts_the_familiar_share(db_session) -> None:
    build_pool(db_session, liked_count=20, discovery_count=40)

    cold = generate_wave(db_session, WaveRequest(temperature=10, length=20, random_seed=3))
    hot = generate_wave(db_session, WaveRequest(temperature=95, length=20, random_seed=3))

    assert cold.target_familiar_percent == 80
    assert hot.target_familiar_percent == 15
    assert cold.actual_familiar_percent > hot.actual_familiar_percent


def test_thin_familiar_pool_reports_the_actual_mix(db_session) -> None:
    """Quotas are a goal, never a reason to break a hard filter."""
    build_pool(db_session, liked_count=1, discovery_count=30)

    result = generate_wave(db_session, WaveRequest(temperature=10, length=20, random_seed=5))
    assert result.target_familiar_percent == 80
    assert result.actual_familiar_percent < 80
    assert "FAMILIAR_POOL_WIDENED" in result.relaxations


def test_generation_and_items_are_persisted(db_session) -> None:
    from app.persistence.models import QueueGeneration, QueueItem

    build_pool(db_session)
    result = generate_wave(db_session, WaveRequest(length=15, random_seed=11))

    generation = db_session.get(QueueGeneration, result.generation_id)
    assert generation is not None
    assert generation.random_seed == 11
    assert generation.serving_policy == "rule-score-v1"
    assert generation.quality_score_source == "RULE_MAPPED"

    items = db_session.query(QueueItem).filter_by(queue_id=result.queue_id).all()
    assert len(items) == len(result.items)


def test_items_carry_deterministic_reason_codes(db_session) -> None:
    build_pool(db_session)
    result = generate_wave(db_session, WaveRequest(length=20, random_seed=2))
    assert all(len(item.reason_codes) <= 3 for item in result.items)
    assert any("LIKED_TRACK" in item.reason_codes for item in result.items)
    assert any(
        code in item.reason_codes
        for item in result.items
        for code in ("RADIO_SOURCE", "RELATED_TO_POSITIVE_SEED", "NEVER_PLAYED")
    )


def test_empty_pool_returns_no_items_instead_of_failing(db_session) -> None:
    result = generate_wave(db_session, WaveRequest(length=20))
    assert result.items == ()


def test_queue_opens_with_tracks_that_already_played(db_session) -> None:
    """Embeddability is only knowable by trying, so lead with proven tracks.

    The proven set only helps once history is longer than the 30-play recency
    exclusion — before that every proven track is also a recent one and is
    filtered out anyway.
    """
    build_pool(db_session, liked_count=10, discovery_count=40)

    # Two old successful plays, then enough newer plays to push them out of
    # the recency window so they become eligible again.
    for index, video_id in enumerate(("liked-7", "liked-8")):
        db_session.add(
            PlaybackSession(
                session_id=f"proven-{index}",
                video_id=video_id,
                started_at=utcnow() - dt.timedelta(days=30),
                played_seconds=120.0,
            )
        )
    for index in range(35):
        db_session.add(
            PlaybackSession(
                session_id=f"filler-{index}",
                video_id=f"filler-{index}",
                started_at=utcnow() - dt.timedelta(hours=index),
                played_seconds=60.0,
            )
        )
    db_session.flush()

    result = generate_wave(db_session, WaveRequest(temperature=20, length=12, random_seed=42))
    head = [item.video_id for item in result.items[:2]]
    assert set(head) <= {"liked-7", "liked-8"}, head


def test_unproven_tracks_still_make_the_queue(db_session) -> None:
    """The head preference must not turn into a permanent exclusion."""
    build_pool(db_session, liked_count=6, discovery_count=20)
    db_session.add(
        PlaybackSession(
            session_id="proven",
            video_id="liked-0",
            started_at=utcnow(),
            played_seconds=90.0,
        )
    )
    db_session.flush()

    result = generate_wave(db_session, WaveRequest(length=20, random_seed=8))
    assert len(result.items) > 5
    assert any(item.video_id != "liked-0" for item in result.items)
