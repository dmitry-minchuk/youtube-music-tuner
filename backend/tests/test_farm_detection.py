"""Artist-level farm detection (docs/05 section 4, docs/11 section 2)."""

from __future__ import annotations

from app.persistence.models import TasteVeto, Track
from app.recommender.farms import apply_farm_vetoes, detect_farm_artists
from app.recommender.slop import mean_title_similarity
from tests.test_wave_generation import seed_track


def _farm(db, artist: str, count: int, template: str) -> None:
    for index in range(count):
        video_id = f"{artist.lower()}-{index}"
        seed_track(db, video_id, artist=artist)
        db.get(Track, video_id).title = template.format(i=index)
    db.flush()


def test_flagged_track_farm_is_vetoed(db_session) -> None:
    """The Gazzarin case: a handful of outright-slop uploads bans the farm."""
    _farm(
        db_session,
        artist="Gazza - A.I. Art. Poesia",
        count=4,
        template="Song {i} – Gazza, Gazza70 (#Bluesrock #Country #RootsRock)",
    )

    verdicts = apply_farm_vetoes(db_session)
    assert [v.reason for v in verdicts] == ["FLAGGED_TRACKS"]
    row = db_session.get(TasteVeto, verdicts[0].representative_video_id)
    assert row is not None and row.source == "FARM_AUTO"


def test_ai_named_channel_is_vetoed_on_volume(db_session) -> None:
    """Every upload of an "A.I."-named channel carries two points; five of
    them make the pattern undeniable."""
    _farm(db_session, artist="promptwave", count=5, template="Original Song {i}")

    verdicts = detect_farm_artists(db_session)
    assert [v.reason for v in verdicts] == ["SUSPECT_TRACKS"]


def test_templated_titles_alone_never_convict(db_session) -> None:
    """A live composer numbering a series must not look like a farm."""
    _farm(db_session, artist="Composer", count=6, template="Symphony No. {i} in D minor")

    assert mean_title_similarity([f"Symphony No. {i} in D minor" for i in range(6)]) >= 0.6
    assert detect_farm_artists(db_session) == []


def test_templated_titles_with_a_slop_marker_convict(db_session) -> None:
    _farm(
        db_session,
        artist="TemplateFarm",
        count=4,
        template="Inspired by Chris Rea - Ballad {i} 🖤 | Roots Rock",
    )

    verdicts = detect_farm_artists(db_session)
    assert len(verdicts) == 1
    assert verdicts[0].reason in {"TEMPLATED_TITLES", "SUSPECT_TRACKS"}


def test_a_liked_track_makes_the_artist_immune(db_session) -> None:
    _farm(
        db_session,
        artist="Redeemed - A.I. Art",
        count=4,
        template="Song {i} (#Country #RootsRock #Original)",
    )
    seed_track(db_session, "redeemed-liked", artist="Redeemed - A.I. Art", liked=True)

    assert detect_farm_artists(db_session) == []


def test_an_overridden_auto_veto_is_never_reapplied(db_session) -> None:
    """The human said no: the detector must not re-veto the artist."""
    _farm(
        db_session,
        artist="Persistent - A.I. Art",
        count=4,
        template="Song {i} (#Country #RootsRock #Original)",
    )
    first = apply_farm_vetoes(db_session)
    assert len(first) == 1
    anchor = first[0].representative_video_id

    row = db_session.get(TasteVeto, anchor)
    row.source = "OVERRIDDEN"  # what DELETE /veto does for FARM_AUTO
    db_session.flush()

    assert detect_farm_artists(db_session) == []
    assert db_session.get(TasteVeto, anchor).source == "OVERRIDDEN"
