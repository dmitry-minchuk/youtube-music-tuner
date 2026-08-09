"""Artist-level farm detection (docs/05 section 4, "Слоп-фильтр").

A farm betrays itself behaviourally: one "artist", dozens of templated
tracks, the same slop markers on most of them. Per-track heuristics catch
the tracks; this module catches the producer, so the whole catalogue leaves
the pool at once instead of leaking in one track at a time.

Detection never overrules a human: artists with a liked track are immune,
and an auto-veto the listener removed (source=OVERRIDDEN) is never
re-applied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Artist,
    LibraryTrackState,
    TasteVeto,
    Track,
    TrackArtist,
    utcnow,
)
from app.recommender.slop import (
    SLOP_EXCLUDE_SCORE,
    SLOP_PENALTY_SCORE,
    mean_title_similarity,
    slop_score,
)

logger = logging.getLogger(__name__)

# An artist is a farm when: three tracks are outright slop; or five carry
# slop markers (an "A.I."-named channel flags every upload at two points);
# or the titles are one filled-in template and at least one track smells.
FARM_FLAGGED_TRACKS = 3
FARM_SUSPECT_TRACKS = 5
FARM_TEMPLATE_MIN_TRACKS = 4
FARM_TEMPLATE_SIMILARITY = 0.6


@dataclass(frozen=True, slots=True)
class FarmVerdict:
    artist_id: str
    artist_name: str
    reason: str
    representative_video_id: str
    track_count: int


def detect_farm_artists(db: Session) -> list[FarmVerdict]:
    """Artists whose catalogue, taken together, is mass-generated output."""
    rows = db.execute(
        select(TrackArtist.artist_id, Artist.name, Track.video_id, Track.title)
        .join(Track, Track.video_id == TrackArtist.track_id)
        .join(Artist, Artist.artist_id == TrackArtist.artist_id)
        .where(TrackArtist.ordinal == 0)
    ).all()

    by_artist: dict[str, list[tuple[str, str]]] = {}
    names: dict[str, str] = {}
    for artist_id, artist_name, video_id, title in rows:
        by_artist.setdefault(artist_id, []).append((video_id, title or ""))
        names[artist_id] = artist_name

    liked_artists = set(
        db.scalars(
            select(TrackArtist.artist_id)
            .join(LibraryTrackState, LibraryTrackState.video_id == TrackArtist.track_id)
            .where(LibraryTrackState.is_liked.is_(True), TrackArtist.ordinal == 0)
        ).all()
    )
    # Any existing veto row — manual, automatic or overridden — means the
    # human already decided about this artist one way or the other.
    decided_artists = set(db.scalars(select(TasteVeto.artist_id)).all())

    verdicts: list[FarmVerdict] = []
    for artist_id, tracks in by_artist.items():
        if len(tracks) < 2:
            continue
        if artist_id in liked_artists or artist_id in decided_artists:
            continue

        name = names[artist_id]
        scored = [(video_id, title, slop_score(title, [name])) for video_id, title in tracks]
        flagged = [item for item in scored if item[2] >= SLOP_EXCLUDE_SCORE]
        suspect = [item for item in scored if item[2] >= SLOP_PENALTY_SCORE]

        reason: str | None = None
        if len(flagged) >= FARM_FLAGGED_TRACKS:
            reason = "FLAGGED_TRACKS"
        elif len(suspect) >= FARM_SUSPECT_TRACKS:
            reason = "SUSPECT_TRACKS"
        elif (
            len(tracks) >= FARM_TEMPLATE_MIN_TRACKS
            and len(suspect) >= 1
            and mean_title_similarity([title for _, title in tracks]) >= FARM_TEMPLATE_SIMILARITY
        ):
            reason = "TEMPLATED_TITLES"

        if reason is None:
            continue
        anchor = max(scored, key=lambda item: (item[2], item[0]))
        verdicts.append(
            FarmVerdict(
                artist_id=artist_id,
                artist_name=name,
                reason=reason,
                representative_video_id=anchor[0],
                track_count=len(tracks),
            )
        )
    return verdicts


def apply_farm_vetoes(db: Session) -> list[FarmVerdict]:
    """Materialise verdicts as FARM_AUTO vetoes; one row anchors an artist."""
    verdicts = detect_farm_artists(db)
    now = utcnow()
    for verdict in verdicts:
        if db.get(TasteVeto, verdict.representative_video_id) is not None:
            continue
        db.add(
            TasteVeto(
                video_id=verdict.representative_video_id,
                artist_id=verdict.artist_id,
                source="FARM_AUTO",
                created_at=now,
            )
        )
        logger.info(
            "farm artist vetoed",
            extra={
                "operation": "farm_detect",
                "artist": verdict.artist_name,
                "reason": verdict.reason,
                "tracks": verdict.track_count,
            },
        )
    db.flush()
    return verdicts
