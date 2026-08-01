"""Fakes used by integration tests: a scripted ytmusicapi client and a
port-level fake catalogue."""

from __future__ import annotations

from typing import Any

from app.domain.catalog import (
    Account,
    ArtistRef,
    CandidateSource,
    PlaylistDiffPlan,
    Rating,
    RemoteHistoryItem,
    RemotePlaylist,
    RemotePlaylistItem,
    RemotePlaylistSnapshot,
    Track,
    TrackCandidate,
)
from tests.fixtures import ytmusic_payloads as payloads


class FakeYtMusicClient:
    """Mimics the ytmusicapi surface the adapter uses."""

    def __init__(self, **overrides: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.responses: dict[str, Any] = {
            "get_account_info": payloads.ACCOUNT,
            "get_liked_songs": payloads.LIKED_SONGS,
            "get_library_playlists": payloads.LIBRARY_PLAYLISTS,
            "get_playlist": payloads.PLAYLIST_DETAIL,
            "get_history": payloads.HISTORY,
            "get_watch_playlist": payloads.WATCH_PLAYLIST_RADIO,
            "get_song_related": payloads.SONG_RELATED,
            "search": payloads.SEARCH_SONGS,
            "rate_song": {"status": "ok"},
            "create_playlist": payloads.CREATE_PLAYLIST_OK,
            "add_playlist_items": payloads.ADD_ITEMS_OK,
            "remove_playlist_items": payloads.REMOVE_ITEMS_OK,
            "edit_playlist": payloads.EDIT_PLAYLIST_OK,
            "delete_playlist": payloads.DELETE_PLAYLIST_OK,
        }
        self.responses.update(overrides)

    def _respond(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))
        value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(*args, **kwargs)
        return value

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def caller(*args: Any, **kwargs: Any) -> Any:
            return self._respond(name, *args, **kwargs)

        return caller

    def call_names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def count(self, name: str) -> int:
        return sum(1 for called, _, _ in self.calls if called == name)


def track(video_id: str, title: str, artist: str = "Alpha", **kwargs: Any) -> Track:
    return Track(
        video_id=video_id,
        title=title,
        artists=(ArtistRef(artist_id=f"UC{artist.lower()}", name=artist),),
        duration_seconds=kwargs.pop("duration_seconds", 200),
        **kwargs,
    )


class FakeCatalog:
    """Port-level fake for tests that do not exercise parsing."""

    def __init__(
        self,
        liked: list[Track] | None = None,
        playlists: list[RemotePlaylist] | None = None,
        history_items: list[RemoteHistoryItem] | None = None,
        candidates: dict[str, list[TrackCandidate]] | None = None,
        snapshots: dict[str, RemotePlaylistSnapshot] | None = None,
    ) -> None:
        self.liked = liked if liked is not None else [track("vid-1", "One")]
        self.playlists = playlists if playlists is not None else []
        self.history_items = history_items or []
        self.candidates = candidates or {}
        self.snapshots = snapshots or {}
        self.ratings: list[tuple[str, Rating]] = []
        self.created: list[tuple[str, str, list[str]]] = []
        self.applied: list[PlaylistDiffPlan] = []
        self.deleted: list[str] = []
        self.rate_error: Exception | None = None
        self.rate_hook = None

    def account(self) -> Account:
        return Account(name="Test Listener", channel_handle="@test-listener")

    def liked_tracks(self, limit: int | None = None) -> list[Track]:
        return list(self.liked)

    def library_playlists(self) -> list[RemotePlaylist]:
        return list(self.playlists)

    def playlist(self, playlist_id: str) -> RemotePlaylistSnapshot:
        if playlist_id in self.snapshots:
            return self.snapshots[playlist_id]
        import datetime as dt

        return RemotePlaylistSnapshot(
            playlist_id=playlist_id,
            title="Empty",
            description=None,
            items=(),
            fetched_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
        )

    def history(self) -> list[RemoteHistoryItem]:
        return list(self.history_items)

    def related(self, video_id: str) -> list[TrackCandidate]:
        return [c for c in self.candidates.get(video_id, []) if c.source is CandidateSource.RELATED]

    def radio(self, video_id: str, limit: int = 25) -> list[TrackCandidate]:
        return [c for c in self.candidates.get(video_id, []) if c.source is CandidateSource.RADIO][
            :limit
        ]

    def search(self, query: str, limit: int = 20) -> list[Track]:
        return [t for t in self.liked if query.casefold() in t.title.casefold()][:limit]

    def rate_track(self, video_id: str, rating: Rating) -> None:
        if self.rate_hook is not None:
            self.rate_hook(video_id, rating)
        if self.rate_error is not None:
            raise self.rate_error
        self.ratings.append((video_id, rating))

    def create_private_playlist(self, title: str, description: str, video_ids: list[str]) -> str:
        self.created.append((title, description, list(video_ids)))
        return f"PL{len(self.created):08d}"

    def apply_playlist_diff(self, plan: PlaylistDiffPlan) -> None:
        self.applied.append(plan)

    def delete_managed_playlist(self, playlist_id: str, expected_marker: str) -> None:
        self.deleted.append(playlist_id)


def snapshot(
    playlist_id: str, video_ids: list[str], description: str = ""
) -> RemotePlaylistSnapshot:
    import datetime as dt

    return RemotePlaylistSnapshot(
        playlist_id=playlist_id,
        title="Tuner",
        description=description,
        items=tuple(
            RemotePlaylistItem(
                position=index,
                track=track(video_id, f"Track {video_id}"),
                set_video_id=f"set-{video_id}",
            )
            for index, video_id in enumerate(video_ids, start=1)
        ),
        fetched_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
    )
