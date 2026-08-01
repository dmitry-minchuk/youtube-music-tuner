"""Parsers turning ytmusicapi dictionaries into domain dataclasses.

Rules from docs/03 section 3: required ``videoId``/title/artists are
validated, a missing album or duration stays ``None`` rather than being
invented, and a malformed payload raises ParseError with no raw content.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.domain.catalog import (
    Account,
    ArtistRef,
    CandidateSource,
    RemoteHistoryItem,
    RemotePlaylist,
    RemotePlaylistItem,
    RemotePlaylistSnapshot,
    Track,
    TrackCandidate,
)
from app.integrations.youtube_music.errors import ParseError


def _as_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ParseError(f"expected an object for {what}")
    return value


def _as_list(value: Any, what: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ParseError(f"expected a list for {what}")
    return value


def parse_artists(raw: Any) -> tuple[ArtistRef, ...]:
    artists: list[ArtistRef] = []
    for entry in _as_list(raw, "artists"):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        # Some entries legitimately carry no channel id; fall back to the name
        # so local aggregation still groups them consistently.
        artist_id = entry.get("id")
        if not isinstance(artist_id, str) or not artist_id:
            artist_id = f"name:{name.strip().casefold()}"
        artists.append(ArtistRef(artist_id=artist_id, name=name.strip()))
    return tuple(artists)


def _duration_seconds(raw: dict[str, Any]) -> int | None:
    value = raw.get("duration_seconds")
    if isinstance(value, int) and value > 0:
        return value
    text = raw.get("duration")
    if isinstance(text, str) and text.count(":") in (1, 2):
        try:
            parts = [int(part) for part in text.split(":")]
        except ValueError:
            return None
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds or None
    return None


def _thumbnail(raw: dict[str, Any]) -> str | None:
    thumbnails = raw.get("thumbnails")
    if not isinstance(thumbnails, list) or not thumbnails:
        return None
    largest = thumbnails[-1]
    if isinstance(largest, dict):
        url = largest.get("url")
        if isinstance(url, str):
            return url
    return None


def parse_track(raw: Any, *, require_video_id: bool = False) -> Track:
    item = _as_dict(raw, "track")

    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ParseError("track is missing a title")

    video_id = item.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        if require_video_id:
            raise ParseError("track is missing videoId")
        video_id = None

    album = item.get("album")
    album_id: str | None = None
    album_title: str | None = None
    if isinstance(album, dict):
        raw_id, raw_name = album.get("id"), album.get("name")
        album_id = raw_id if isinstance(raw_id, str) and raw_id else None
        album_title = raw_name if isinstance(raw_name, str) and raw_name else None

    is_available = item.get("isAvailable")
    playable = video_id is not None and (is_available is None or bool(is_available))

    return Track(
        video_id=video_id,
        title=title.strip(),
        artists=parse_artists(item.get("artists")),
        duration_seconds=_duration_seconds(item),
        album_id=album_id,
        album_title=album_title,
        thumbnail_url=_thumbnail(item),
        is_playable=playable,
    )


def parse_account(raw: Any) -> Account:
    item = _as_dict(raw, "account")
    name = item.get("accountName")
    if not isinstance(name, str) or not name.strip():
        raise ParseError("account payload is missing accountName")
    handle = item.get("channelHandle")
    return Account(
        name=name.strip(),
        channel_handle=handle if isinstance(handle, str) and handle else None,
    )


def parse_liked_songs(raw: Any) -> list[Track]:
    payload = _as_dict(raw, "liked songs")
    tracks: list[Track] = []
    for entry in _as_list(payload.get("tracks"), "liked tracks"):
        try:
            tracks.append(parse_track(entry))
        except ParseError:
            # One unreadable row must not lose the whole library snapshot.
            continue
    return tracks


def parse_library_playlists(raw: Any) -> list[RemotePlaylist]:
    playlists: list[RemotePlaylist] = []
    for entry in _as_list(raw, "library playlists"):
        if not isinstance(entry, dict):
            continue
        playlist_id = entry.get("playlistId")
        title = entry.get("title")
        if not isinstance(playlist_id, str) or not playlist_id:
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        count = entry.get("count")
        if isinstance(count, str):
            digits = "".join(ch for ch in count if ch.isdigit())
            count = int(digits) if digits else 0
        description = entry.get("description")
        playlists.append(
            RemotePlaylist(
                playlist_id=playlist_id,
                title=title.strip(),
                description=description if isinstance(description, str) else None,
                track_count=count if isinstance(count, int) else 0,
            )
        )
    return playlists


def parse_playlist_snapshot(
    raw: Any, playlist_id: str, fetched_at: dt.datetime
) -> RemotePlaylistSnapshot:
    payload = _as_dict(raw, "playlist")
    title = payload.get("title")
    if not isinstance(title, str):
        raise ParseError("playlist payload is missing a title")
    description = payload.get("description")

    items: list[RemotePlaylistItem] = []
    for position, entry in enumerate(_as_list(payload.get("tracks"), "playlist tracks"), start=1):
        try:
            track = parse_track(entry)
        except ParseError:
            continue
        set_video_id = entry.get("setVideoId") if isinstance(entry, dict) else None
        items.append(
            RemotePlaylistItem(
                position=position,
                track=track,
                set_video_id=set_video_id if isinstance(set_video_id, str) else None,
            )
        )

    remote_id = payload.get("id")
    return RemotePlaylistSnapshot(
        playlist_id=remote_id if isinstance(remote_id, str) and remote_id else playlist_id,
        title=title,
        description=description if isinstance(description, str) else None,
        items=tuple(items),
        fetched_at=fetched_at,
    )


def parse_history(raw: Any) -> list[RemoteHistoryItem]:
    items: list[RemoteHistoryItem] = []
    for order, entry in enumerate(_as_list(raw, "history"), start=1):
        try:
            track = parse_track(entry, require_video_id=True)
        except ParseError:
            continue
        played = entry.get("played") if isinstance(entry, dict) else None
        items.append(
            RemoteHistoryItem(
                track=track,
                play_order=order,
                period_label=played if isinstance(played, str) else None,
            )
        )
    return items


def parse_watch_playlist(
    raw: Any, source: CandidateSource, source_key: str
) -> list[TrackCandidate]:
    payload = _as_dict(raw, "watch playlist")
    return _candidates(payload.get("tracks"), source, source_key)


def parse_related_sections(raw: Any, source_key: str) -> list[TrackCandidate]:
    """``get_song_related`` returns shelves; only song shelves are useful.

    Shelves are heterogeneous: alongside track lists YouTube returns things
    like "About the artist", whose ``contents`` is a biography string. A
    non-list shelf is a normal shape, not a broken payload, so it is skipped
    rather than treated as a parse failure.
    """
    candidates: list[TrackCandidate] = []
    rank = 0
    for section in _as_list(raw, "related sections"):
        if not isinstance(section, dict):
            continue
        contents = section.get("contents")
        if not isinstance(contents, list):
            continue
        for entry in contents:
            if not isinstance(entry, dict) or not entry.get("videoId"):
                continue
            try:
                track = parse_track(entry, require_video_id=True)
            except ParseError:
                continue
            rank += 1
            candidates.append(
                TrackCandidate(
                    track=track,
                    source=CandidateSource.RELATED,
                    rank=rank,
                    source_key=source_key,
                )
            )
    return candidates


def parse_search_results(raw: Any) -> list[Track]:
    tracks: list[Track] = []
    for entry in _as_list(raw, "search results"):
        if not isinstance(entry, dict):
            continue
        if entry.get("resultType") not in (None, "song", "video"):
            continue
        try:
            tracks.append(parse_track(entry, require_video_id=True))
        except ParseError:
            continue
    return tracks


def _candidates(raw: Any, source: CandidateSource, source_key: str) -> list[TrackCandidate]:
    candidates: list[TrackCandidate] = []
    rank = 0
    for entry in _as_list(raw, "candidates"):
        if not isinstance(entry, dict):
            continue
        try:
            track = parse_track(entry, require_video_id=True)
        except ParseError:
            continue
        rank += 1
        candidates.append(
            TrackCandidate(track=track, source=source, rank=rank, source_key=source_key)
        )
    return candidates
