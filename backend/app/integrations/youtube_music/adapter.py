"""ytmusicapi adapter — the only module that knows the library exists.

Every call goes through :meth:`_call`, which classifies failures into typed
integration errors and reports timing/outcome to a recorder for the ledger.
Raw payloads are never logged.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Protocol, TypeVar

from app.domain.catalog import (
    Account,
    CandidateSource,
    PlaylistDiffPlan,
    Rating,
    RemoteHistoryItem,
    RemotePlaylist,
    RemotePlaylistSnapshot,
    Track,
    TrackCandidate,
)
from app.integrations.youtube_music import parsers
from app.integrations.youtube_music.errors import (
    AuthError,
    IntegrationError,
    ParseError,
    RateLimited,
    RemoteChanged,
    Unavailable,
)
from app.settings import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_AUTH_MARKERS = ("unauthorized", "401", "invalid_grant", "token", "forbidden", "403")
_RATE_MARKERS = ("429", "too many requests", "quota", "rate limit")
_UNAVAILABLE_MARKERS = ("timeout", "timed out", "connection", "503", "502", "500", "temporarily")


class CallRecorder(Protocol):
    """Ledger sink; implemented by the persistence layer."""

    def record(
        self,
        *,
        operation: str,
        duration_ms: int,
        outcome: str,
        is_mutation: bool,
        playlist_id: str | None = None,
        error_code: str | None = None,
        counts_against_automatic_budget: bool = True,
    ) -> None: ...


class NullRecorder:
    def record(self, **_: Any) -> None:  # noqa: D102
        return None


def classify_exception(exc: Exception) -> IntegrationError:
    """Map an arbitrary library/transport failure onto our error taxonomy."""
    text = f"{type(exc).__name__}: {exc}".casefold()
    if any(marker in text for marker in _AUTH_MARKERS):
        return AuthError("YouTube Music authentication failed")
    if any(marker in text for marker in _RATE_MARKERS):
        return RateLimited("YouTube Music rate limited the request")
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return Unavailable("YouTube Music is temporarily unavailable")
    if isinstance(exc, KeyError | IndexError | TypeError | ValueError):
        return ParseError("YouTube Music returned an unreadable payload")
    return Unavailable("YouTube Music call failed")


class YouTubeMusicAdapter:
    """Implements :class:`MusicCatalogPort` on top of ytmusicapi 1.12.1."""

    def __init__(
        self,
        settings: Settings,
        recorder: CallRecorder | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._recorder = recorder or NullRecorder()
        self._client_factory = client_factory or self._build_client
        self._client: Any | None = None

    # -- client -----------------------------------------------------------

    def _build_client(self) -> Any:
        from ytmusicapi import OAuthCredentials, YTMusic

        # Browser headers first: YouTube Music rejects Bearer tokens issued to
        # self-made OAuth clients with HTTP 400, so cookie auth is the path
        # that actually works (docs/03 section 2).
        browser_path: Path = self._settings.browser_auth_file
        if browser_path.is_file():
            return YTMusic(str(browser_path))

        oauth_path: Path = self._settings.oauth_file
        client_path: Path = self._settings.client_secret_file
        if not oauth_path.is_file():
            raise AuthError("not connected; import browser headers or run the device flow")
        if not client_path.is_file():
            raise AuthError("OAuth client file is missing; import client credentials")

        try:
            client_config = json.loads(client_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthError("OAuth client file is unreadable") from exc

        credentials = OAuthCredentials(
            client_id=str(client_config["client_id"]),
            client_secret=str(client_config["client_secret"]),
        )
        return YTMusic(str(oauth_path), oauth_credentials=credentials)

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def reset_client(self) -> None:
        self._client = None

    # -- call plumbing ----------------------------------------------------

    def _call(
        self,
        operation: str,
        func: Callable[[], T],
        *,
        is_mutation: bool = False,
        playlist_id: str | None = None,
        counts_against_automatic_budget: bool = True,
    ) -> T:
        started = time.monotonic()
        try:
            result = func()
        except IntegrationError as exc:
            self._report(
                operation,
                started,
                "FAILED",
                is_mutation,
                playlist_id,
                exc.code,
                counts_against_automatic_budget,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately narrowed below
            error = classify_exception(exc)
            if isinstance(error, AuthError):
                self.reset_client()
            self._report(
                operation,
                started,
                "FAILED",
                is_mutation,
                playlist_id,
                error.code,
                counts_against_automatic_budget,
            )
            logger.warning(
                "external call failed",
                extra={
                    "operation": operation,
                    "outcome": "FAILED",
                    "error_code": error.code,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise error from None

        self._report(
            operation,
            started,
            "SUCCESS",
            is_mutation,
            playlist_id,
            None,
            counts_against_automatic_budget,
        )
        return result

    def _report(
        self,
        operation: str,
        started: float,
        outcome: str,
        is_mutation: bool,
        playlist_id: str | None,
        error_code: str | None,
        counts_against_automatic_budget: bool,
    ) -> None:
        self._recorder.record(
            operation=operation,
            duration_ms=int((time.monotonic() - started) * 1000),
            outcome=outcome,
            is_mutation=is_mutation,
            playlist_id=playlist_id,
            error_code=error_code,
            counts_against_automatic_budget=counts_against_automatic_budget,
        )

    # -- read operations --------------------------------------------------

    def account(self) -> Account:
        raw = self._call("get_account_info", lambda: self.client.get_account_info())
        return parsers.parse_account(raw)

    def liked_tracks(self, limit: int | None = None) -> list[Track]:
        size = limit or 5000
        raw = self._call("get_liked_songs", lambda: self.client.get_liked_songs(limit=size))
        return parsers.parse_liked_songs(raw)

    def library_playlists(self) -> list[RemotePlaylist]:
        raw = self._call(
            "get_library_playlists", lambda: self.client.get_library_playlists(limit=None)
        )
        return parsers.parse_library_playlists(raw)

    def playlist(self, playlist_id: str) -> RemotePlaylistSnapshot:
        raw = self._call(
            "get_playlist",
            lambda: self.client.get_playlist(playlist_id, limit=None),
            playlist_id=playlist_id,
        )
        fetched_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        return parsers.parse_playlist_snapshot(raw, playlist_id, fetched_at)

    def history(self) -> list[RemoteHistoryItem]:
        raw = self._call("get_history", lambda: self.client.get_history())
        return parsers.parse_history(raw)

    def radio(self, video_id: str, limit: int = 25) -> list[TrackCandidate]:
        raw = self._call(
            "get_watch_playlist",
            lambda: self.client.get_watch_playlist(videoId=video_id, radio=True, limit=limit),
        )
        return parsers.parse_watch_playlist(raw, CandidateSource.RADIO, video_id)

    def related(self, video_id: str) -> list[TrackCandidate]:
        """Related needs the browseId that the watch playlist exposes."""
        watch = self._call(
            "get_watch_playlist",
            lambda: self.client.get_watch_playlist(videoId=video_id, radio=False, limit=1),
        )
        browse_id = watch.get("related") if isinstance(watch, dict) else None
        if not isinstance(browse_id, str) or not browse_id:
            return []
        raw = self._call("get_song_related", lambda: self.client.get_song_related(browse_id))
        return parsers.parse_related_sections(raw, video_id)

    def search(self, query: str, limit: int = 20) -> list[Track]:
        raw = self._call(
            "search",
            lambda: self.client.search(query, filter="songs", limit=limit),
        )
        return parsers.parse_search_results(raw)

    # -- write operations -------------------------------------------------

    def rate_track(self, video_id: str, rating: Rating) -> None:
        from ytmusicapi import LikeStatus

        mapping = {
            Rating.LIKE: LikeStatus.LIKE,
            Rating.DISLIKE: LikeStatus.DISLIKE,
            Rating.INDIFFERENT: LikeStatus.INDIFFERENT,
        }
        self._call(
            "rate_song",
            lambda: self.client.rate_song(video_id, mapping[rating]),
            is_mutation=True,
        )

    def create_private_playlist(self, title: str, description: str, video_ids: list[str]) -> str:
        result = self._call(
            "create_playlist",
            lambda: self.client.create_playlist(
                title=title,
                description=description,
                privacy_status="PRIVATE",
                video_ids=video_ids,
            ),
            is_mutation=True,
        )
        if isinstance(result, str) and result:
            return result
        # A dict here means the request was rejected; the body is not logged.
        raise RemoteChanged("playlist creation did not return an id")

    def apply_playlist_diff(self, plan: PlaylistDiffPlan) -> None:
        """Apply removes, then adds, then moves — one request per move."""
        removals = [op for op in plan.operations if op.kind == "REMOVE"]
        additions = [op for op in plan.operations if op.kind == "ADD"]
        moves = [op for op in plan.operations if op.kind == "MOVE"]

        if removals:
            videos = [
                {"videoId": op.video_id, "setVideoId": op.set_video_id}
                for op in removals
                if op.set_video_id
            ]
            if len(videos) != len(removals):
                raise RemoteChanged("removal plan is missing setVideoId for some items")
            self._call(
                "remove_playlist_items",
                lambda: self.client.remove_playlist_items(plan.playlist_id, videos),
                is_mutation=True,
                playlist_id=plan.playlist_id,
            )

        if additions:
            video_ids = [op.video_id for op in additions]
            self._call(
                "add_playlist_items",
                lambda: self.client.add_playlist_items(plan.playlist_id, video_ids),
                is_mutation=True,
                playlist_id=plan.playlist_id,
            )

        for move in moves:
            if not move.set_video_id:
                raise RemoteChanged("move plan is missing setVideoId")
            move_item = (move.set_video_id, move.before_set_video_id)
            self._call(
                "edit_playlist_move",
                partial(self.client.edit_playlist, plan.playlist_id, moveItem=move_item),
                is_mutation=True,
                playlist_id=plan.playlist_id,
            )

    def delete_managed_playlist(self, playlist_id: str, expected_marker: str) -> None:
        """Fresh marker read, then exactly one delete (docs/03 section 8)."""
        snapshot = self._call(
            "get_playlist",
            lambda: self.client.get_playlist(playlist_id, limit=1),
            playlist_id=playlist_id,
            counts_against_automatic_budget=False,
        )
        description = snapshot.get("description") if isinstance(snapshot, dict) else None
        if not isinstance(description, str) or expected_marker not in description:
            raise RemoteChanged("ownership marker does not match; refusing to delete")

        self._call(
            "delete_playlist",
            lambda: self.client.delete_playlist(playlist_id),
            is_mutation=True,
            playlist_id=playlist_id,
            counts_against_automatic_budget=False,
        )
