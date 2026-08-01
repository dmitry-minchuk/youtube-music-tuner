"""Session classification and reward (docs/04 sections 4-7, docs/11 section 2)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.player.aggregation import SessionAccumulator, fold_event, summarize
from app.player.reward import RewardInputs, compute_reward, implicit_raw

BASE = dt.datetime(2026, 8, 1, 12, 0, 0)


def build(events: list[dict], video_id: str = "v1") -> SessionAccumulator:
    accumulator = SessionAccumulator(session_id="s1", video_id=video_id)
    for index, event in enumerate(events):
        event.setdefault("occurred_at", BASE + dt.timedelta(seconds=index))
        fold_event(accumulator, event)
    return accumulator


def tick(**payload) -> dict:
    return {"type": "progress_tick", "payload": payload}


def with_duration(seconds: float, source: str = "PLAYER", **payload) -> dict:
    return tick(effectiveDurationSeconds=seconds, durationSource=source, **payload)


# -- played time ----------------------------------------------------------


def test_forward_seek_does_not_increase_played_seconds() -> None:
    """played_seconds comes from verified PLAYING intervals, not position."""
    summary = summarize(
        build(
            [
                with_duration(200, playedSeconds=20, positionSeconds=20),
                with_duration(200, playedSeconds=22, positionSeconds=180, seekForwardSeconds=158),
            ]
        )
    )
    # Only the two verified seconds count, not the 158 second position jump.
    assert summary.played_seconds == 22
    assert summary.played_ratio == pytest.approx(22 / 200)


def test_buffering_and_pause_are_not_playback() -> None:
    summary = summarize(
        build(
            [
                with_duration(200, playedSeconds=15),
                {"type": "buffering_started"},
                {"type": "paused"},
                with_duration(200, playedSeconds=15, bufferedSeconds=9),
            ]
        )
    )
    assert summary.played_seconds == 15
    assert summary.early_skip is False
    assert summary.reward is not None and summary.reward == 0.0


def test_progress_ticks_are_cumulative_and_replay_safe() -> None:
    """Re-delivering the same ticks must not double count."""
    events = [
        with_duration(200, playedSeconds=15),
        with_duration(200, playedSeconds=30),
        with_duration(200, playedSeconds=45),
    ]
    once = summarize(build(events))
    twice = summarize(build([*events, *events]))
    assert once.played_seconds == 45
    assert twice.played_seconds == 45


def test_out_of_order_ticks_keep_the_highest_cumulative_value() -> None:
    summary = summarize(
        build([with_duration(200, playedSeconds=45), with_duration(200, playedSeconds=30)])
    )
    assert summary.played_seconds == 45


def test_played_seconds_cannot_exceed_duration_plus_margin() -> None:
    summary = summarize(build([with_duration(100, playedSeconds=10_000)]))
    assert summary.played_seconds == 105


def test_played_seconds_without_duration_is_capped_by_wall_clock() -> None:
    summary = summarize(build([tick(playedSeconds=10_000, wallClockSeconds=90)]))
    assert summary.played_seconds == 95


# -- skip classification --------------------------------------------------


def test_explicit_next_before_20_percent_is_an_early_skip() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=30), {"type": "next_clicked"}]))
    assert summary.early_skip is True
    assert summary.mid_skip is False
    assert summary.reward == pytest.approx(-0.635149, abs=1e-3)


def test_explicit_next_between_20_and_60_percent_is_a_mid_skip() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=80), {"type": "next_clicked"}]))
    assert summary.mid_skip is True
    assert summary.early_skip is False


def test_page_close_never_creates_a_skip() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=12), {"type": "page_closing"}]))
    assert summary.early_skip is False
    assert summary.mid_skip is False
    assert summary.termination_reason == "abandoned_unknown"
    assert summary.reward == 0.0


def test_player_error_is_neutral_for_taste() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=11), {"type": "player_error"}]))
    assert summary.early_skip is False
    assert summary.reward == 0.0


def test_hidden_tab_pause_is_neutral() -> None:
    summary = summarize(
        build(
            [
                with_duration(200, playedSeconds=14),
                {"type": "visibility_changed", "payload": {"state": "hidden"}},
                {"type": "paused"},
            ]
        )
    )
    assert summary.early_skip is False
    assert summary.reward == 0.0


# -- qualification --------------------------------------------------------


def test_explicit_next_qualifies_even_before_ten_seconds() -> None:
    """A deliberate early skip must reach the model (BR-006)."""
    summary = summarize(build([with_duration(200, playedSeconds=4), {"type": "next_clicked"}]))
    assert summary.qualified is True
    assert summary.early_skip is True
    assert summary.reward is not None and summary.reward < 0


def test_short_session_without_explicit_action_is_not_qualified() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=4), {"type": "player_error"}]))
    assert summary.qualified is False
    assert summary.reward is None


def test_explicit_rating_qualifies_a_short_session() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=3), {"type": "like_set"}]))
    assert summary.qualified is True


# -- completion -----------------------------------------------------------


def test_ninety_percent_played_is_completion() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=185)]))
    assert summary.completed is True
    assert summary.reward == pytest.approx(0.462117, abs=1e-3)


def test_ended_after_large_forward_seek_is_not_a_completion() -> None:
    summary = summarize(
        build(
            [
                with_duration(200, playedSeconds=100, seekForwardSeconds=95),
                {"type": "ended"},
            ]
        )
    )
    assert summary.large_forward_seek is True
    assert summary.completed is False


def test_ended_with_small_seek_above_seventy_percent_completes() -> None:
    summary = summarize(
        build(
            [
                with_duration(200, playedSeconds=150, seekForwardSeconds=10),
                {"type": "ended"},
            ]
        )
    )
    assert summary.large_forward_seek is False
    assert summary.completed is True


def test_large_seek_threshold_is_max_of_30s_and_20_percent() -> None:
    # 20% of 400s = 80s, so 40s of seeking is not "large" here.
    small = summarize(build([with_duration(400, playedSeconds=100, seekForwardSeconds=40)]))
    assert small.large_forward_seek is False
    # For a 100s track the floor of 30s applies.
    large = summarize(build([with_duration(100, playedSeconds=50, seekForwardSeconds=31)]))
    assert large.large_forward_seek is True


def test_partial_listen_scores_less_than_completion() -> None:
    partial = summarize(build([with_duration(200, playedSeconds=140)]))
    complete = summarize(build([with_duration(200, playedSeconds=190)]))
    assert partial.completed is False
    assert partial.reward is not None and complete.reward is not None
    assert partial.reward < complete.reward


# -- unknown duration -----------------------------------------------------


def test_player_duration_wins_over_metadata() -> None:
    accumulator = build(
        [
            with_duration(300, source="METADATA", playedSeconds=10),
            with_duration(200, source="PLAYER", playedSeconds=20),
            with_duration(300, source="METADATA", playedSeconds=30),
        ]
    )
    summary = summarize(accumulator)
    assert summary.duration_source == "PLAYER"
    assert summary.effective_duration_seconds == 200


def test_unknown_duration_uses_absolute_time_rules() -> None:
    summary = summarize(build([tick(playedSeconds=20), {"type": "next_clicked"}]))
    assert summary.classification_basis == "ABSOLUTE_TIME"
    assert summary.played_ratio is None
    assert summary.early_skip is True
    assert summary.reward == pytest.approx(-0.462117, abs=1e-3)  # tanh(-2/4)


def test_unknown_duration_mid_skip_window() -> None:
    summary = summarize(build([tick(playedSeconds=60), {"type": "next_clicked"}]))
    assert summary.mid_skip is True
    assert summary.reward == pytest.approx(-0.124353, abs=1e-3)  # tanh(-0.5/4)


def test_unknown_duration_next_after_two_minutes_is_neutral() -> None:
    summary = summarize(build([tick(playedSeconds=150), {"type": "next_clicked"}]))
    assert summary.early_skip is False
    assert summary.mid_skip is False
    assert summary.reward == 0.0


def test_unknown_duration_ended_below_sixty_seconds_is_unqualified_completion() -> None:
    summary = summarize(build([tick(playedSeconds=40), {"type": "ended"}]))
    assert summary.completed is False
    assert summary.ended_unqualified is True
    assert summary.reward == 0.0


def test_unknown_duration_ended_above_sixty_seconds_completes() -> None:
    summary = summarize(build([tick(playedSeconds=90), {"type": "ended"}]))
    assert summary.completed is True
    assert summary.ended_unqualified is False


# -- reward formula -------------------------------------------------------


def test_explicit_rating_dominates_implicit_signals() -> None:
    disliked = summarize(build([with_duration(200, playedSeconds=195), {"type": "dislike_set"}]))
    assert disliked.reward == pytest.approx(-0.964028, abs=1e-3)

    liked_and_skipped = summarize(
        build(
            [with_duration(200, playedSeconds=10), {"type": "like_set"}, {"type": "next_clicked"}]
        )
    )
    assert liked_and_skipped.reward is not None and liked_and_skipped.reward > 0


def test_completion_and_partial_listen_never_stack() -> None:
    assert implicit_raw(RewardInputs(completed=True, partial_listen=True)) == 2.0


def test_implicit_raw_is_clamped_to_the_reachable_range() -> None:
    maximum = implicit_raw(RewardInputs(completed=True, replayed=True, seek_backward_count=9))
    assert maximum == 6.0
    minimum = implicit_raw(RewardInputs(early_skip=True))
    assert minimum == -3.0


def test_seek_back_contribution_is_capped() -> None:
    assert implicit_raw(RewardInputs(seek_backward_count=1)) == 0.5
    assert implicit_raw(RewardInputs(seek_backward_count=5)) == 1.0


def test_documented_reward_values() -> None:
    """The five worked examples from docs/05 section 6."""
    assert compute_reward(RewardInputs(completed=True)) == pytest.approx(0.462, abs=1e-3)
    assert compute_reward(RewardInputs(replayed=True)) == pytest.approx(0.635, abs=1e-3)
    assert compute_reward(RewardInputs(early_skip=True)) == pytest.approx(-0.635, abs=1e-3)
    assert compute_reward(RewardInputs(explicit_rating="LIKE")) == pytest.approx(0.848, abs=1e-3)
    assert compute_reward(RewardInputs(explicit_rating="DISLIKE")) == pytest.approx(
        -0.964, abs=1e-3
    )


def test_like_replay_and_completion_stay_distinguishable() -> None:
    values = {
        "completion": compute_reward(RewardInputs(completed=True)),
        "replay": compute_reward(RewardInputs(replayed=True)),
        "like": compute_reward(RewardInputs(explicit_rating="LIKE")),
    }
    assert len(set(values.values())) == 3
    assert values["completion"] < values["replay"] < values["like"]


def test_like_keeps_a_gradient_from_implicit_signals() -> None:
    plain = compute_reward(RewardInputs(explicit_rating="LIKE"))
    with_completion = compute_reward(RewardInputs(explicit_rating="LIKE", completed=True))
    assert with_completion > plain


def test_replay_is_recorded() -> None:
    summary = summarize(build([with_duration(200, playedSeconds=190), {"type": "replay_started"}]))
    assert summary.replayed is True
    assert summary.reward is not None and summary.reward > 0.6


def test_player_error_terminates_as_player_error_not_a_skip() -> None:
    """An unplayable track (embedding disabled) must not look like rejection."""
    summary = summarize(build([with_duration(200, playedSeconds=0), {"type": "player_error"}]))
    assert summary.termination_reason == "player_error"
    assert summary.early_skip is False
    assert summary.mid_skip is False
    assert summary.qualified is False
    assert summary.reward is None


def test_an_error_followed_by_a_real_next_still_counts_the_next() -> None:
    summary = summarize(
        build(
            [
                with_duration(200, playedSeconds=8),
                {"type": "player_error"},
                {"type": "next_clicked"},
            ]
        )
    )
    assert summary.early_skip is True
