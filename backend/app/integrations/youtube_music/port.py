"""The interface the domain depends on (docs/03 section 3).

Neither the domain nor the recommender imports ytmusicapi; they only see
this protocol and the dataclasses from ``app.domain.catalog``.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.catalog import (
    Account,
    PlaylistDiffPlan,
    Rating,
    RemoteHistoryItem,
    RemotePlaylist,
    RemotePlaylistSnapshot,
    Track,
    TrackCandidate,
)


class MusicCatalogPort(Protocol):
    def account(self) -> Account: ...

    def liked_tracks(self, limit: int | None = None) -> list[Track]: ...

    def library_playlists(self) -> list[RemotePlaylist]: ...

    def playlist(self, playlist_id: str) -> RemotePlaylistSnapshot: ...

    def history(self) -> list[RemoteHistoryItem]: ...

    def related(self, video_id: str) -> list[TrackCandidate]: ...

    def radio(self, video_id: str, limit: int) -> list[TrackCandidate]: ...

    def search(self, query: str, limit: int = 20) -> list[Track]: ...

    def rate_track(self, video_id: str, rating: Rating) -> None: ...

    def create_private_playlist(
        self, title: str, description: str, video_ids: list[str]
    ) -> str: ...

    def apply_playlist_diff(self, plan: PlaylistDiffPlan) -> None: ...

    def delete_managed_playlist(self, playlist_id: str, expected_marker: str) -> None: ...
