"""Domain models for the music catalogue.

These types are what the domain speaks. The ytmusicapi adapter converts raw
dictionaries into them; nothing outside the adapter sees library payloads
(docs/03 section 3).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum


class Rating(StrEnum):
    LIKE = "LIKE"
    DISLIKE = "DISLIKE"
    INDIFFERENT = "INDIFFERENT"


class CandidateSource(StrEnum):
    RELATED = "RELATED"
    RADIO = "RADIO"
    MOOD = "MOOD"
    PLAYLIST = "PLAYLIST"


@dataclass(frozen=True, slots=True)
class ArtistRef:
    artist_id: str
    name: str


@dataclass(frozen=True, slots=True)
class Track:
    """A playable track. ``video_id`` is None for metadata-only entries."""

    video_id: str | None
    title: str
    artists: tuple[ArtistRef, ...]
    duration_seconds: int | None = None
    album_id: str | None = None
    album_title: str | None = None
    thumbnail_url: str | None = None
    is_playable: bool = True

    @property
    def primary_artist(self) -> ArtistRef | None:
        return self.artists[0] if self.artists else None

    @property
    def is_metadata_only(self) -> bool:
        return self.video_id is None or not self.is_playable


@dataclass(frozen=True, slots=True)
class Account:
    name: str
    channel_handle: str | None = None


@dataclass(frozen=True, slots=True)
class RemotePlaylist:
    playlist_id: str
    title: str
    description: str | None = None
    track_count: int = 0


@dataclass(frozen=True, slots=True)
class RemotePlaylistItem:
    position: int
    track: Track
    set_video_id: str | None = None


@dataclass(frozen=True, slots=True)
class RemotePlaylistSnapshot:
    playlist_id: str
    title: str
    description: str | None
    items: tuple[RemotePlaylistItem, ...]
    fetched_at: dt.datetime

    @property
    def video_ids(self) -> tuple[str, ...]:
        return tuple(item.track.video_id for item in self.items if item.track.video_id)


@dataclass(frozen=True, slots=True)
class RemoteHistoryItem:
    """Only the fact of a recent play — never a duration or a skip."""

    track: Track
    play_order: int
    period_label: str | None = None


@dataclass(frozen=True, slots=True)
class TrackCandidate:
    track: Track
    source: CandidateSource
    rank: int
    source_key: str = ""


@dataclass(frozen=True, slots=True)
class PlaylistOperation:
    """One logical item change in a publish plan."""

    kind: str  # ADD | REMOVE | MOVE
    video_id: str
    set_video_id: str | None = None
    before_set_video_id: str | None = None


@dataclass(frozen=True, slots=True)
class PlaylistDiffPlan:
    playlist_id: str
    expected_marker: str
    operations: tuple[PlaylistOperation, ...] = field(default_factory=tuple)

    @property
    def item_change_count(self) -> int:
        return len(self.operations)

    @property
    def estimated_request_count(self) -> int:
        """Batched add/remove cost one request each; every move costs one."""
        moves = sum(1 for op in self.operations if op.kind == "MOVE")
        has_add = any(op.kind == "ADD" for op in self.operations)
        has_remove = any(op.kind == "REMOVE" for op in self.operations)
        return moves + int(has_add) + int(has_remove)
