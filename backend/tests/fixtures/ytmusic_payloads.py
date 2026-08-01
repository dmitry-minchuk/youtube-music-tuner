"""Anonymised ytmusicapi payload fixtures (docs/11 section 3).

No OAuth material, cookies, account identity or real private playlist ids.
Shapes mirror ytmusicapi 1.12.1 responses.
"""

from __future__ import annotations

from typing import Any

ACCOUNT: dict[str, Any] = {
    "accountName": "Test Listener",
    "channelHandle": "@test-listener",
    "accountPhotoUrl": "https://example.invalid/photo.jpg",
}

# Covers: full metadata, missing album, missing duration, several artists,
# localized title, and an unavailable item.
LIKED_SONGS: dict[str, Any] = {
    "title": "Liked Music",
    "trackCount": 5,
    "tracks": [
        {
            "videoId": "vid-complete",
            "title": "Complete Song",
            "artists": [{"name": "Alpha", "id": "UCalpha"}],
            "album": {"name": "First Album", "id": "MPREb_album1"},
            "duration": "3:42",
            "duration_seconds": 222,
            "thumbnails": [{"url": "https://example.invalid/s.jpg", "width": 60, "height": 60}],
            "isAvailable": True,
            "likeStatus": "LIKE",
            "setVideoId": "set-1",
        },
        {
            "videoId": "vid-no-album",
            "title": "Single Without Album",
            "artists": [{"name": "Beta", "id": "UCbeta"}, {"name": "Gamma", "id": "UCgamma"}],
            "album": None,
            "duration": "4:05",
            "isAvailable": True,
        },
        {
            "videoId": "vid-no-duration",
            "title": "Unknown Length",
            "artists": [{"name": "Delta", "id": "UCdelta"}],
            "album": {"name": "Second Album", "id": "MPREb_album2"},
            "isAvailable": True,
        },
        {
            "videoId": "vid-localized",
            "title": "Пісня про весну",
            "artists": [{"name": "Ансамбль", "id": "UCcyr"}],
            "duration": "2:58",
            "isAvailable": True,
        },
        {
            "videoId": "vid-unavailable",
            "title": "Removed Song",
            "artists": [{"name": "Alpha", "id": "UCalpha"}],
            "isAvailable": False,
        },
    ],
}

LIBRARY_PLAYLISTS: list[dict[str, Any]] = [
    {
        "playlistId": "PLtest0001",
        "title": "Morning",
        "description": "hand made",
        "count": "23",
        "thumbnails": [],
    },
    {
        "playlistId": "PLtest0002",
        "title": "Focus",
        "description": None,
        "count": 8,
        "thumbnails": [],
    },
    # Rows without a usable id must be skipped, not crash the sync.
    {"title": "Broken row", "count": 3},
]

PLAYLIST_DETAIL: dict[str, Any] = {
    "id": "PLtest0001",
    "privacy": "PRIVATE",
    "title": "Morning",
    "description": "Managed by YouTube Music Tuner; instance=inst-1; schema=1",
    "trackCount": 3,
    "tracks": [
        {
            "videoId": "vid-complete",
            "title": "Complete Song",
            "artists": [{"name": "Alpha", "id": "UCalpha"}],
            "album": {"name": "First Album", "id": "MPREb_album1"},
            "duration": "3:42",
            "setVideoId": "set-a",
            "isAvailable": True,
        },
        {
            "videoId": "vid-no-album",
            "title": "Single Without Album",
            "artists": [{"name": "Beta", "id": "UCbeta"}],
            "duration": "4:05",
            "setVideoId": "set-b",
            "isAvailable": True,
        },
        {
            "videoId": "vid-deleted",
            "title": "Deleted Item",
            "artists": [],
            "setVideoId": "set-c",
            "isAvailable": False,
        },
    ],
}

HISTORY: list[dict[str, Any]] = [
    {
        "videoId": "vid-complete",
        "title": "Complete Song",
        "artists": [{"name": "Alpha", "id": "UCalpha"}],
        "duration": "3:42",
        "played": "Today",
    },
    {
        "videoId": "vid-localized",
        "title": "Пісня про весну",
        "artists": [{"name": "Ансамбль", "id": "UCcyr"}],
        "played": "Yesterday",
    },
]

WATCH_PLAYLIST_RADIO: dict[str, Any] = {
    "playlistId": "RDAMVMvid-complete",
    "related": "MPTRt_related_seed",
    "tracks": [
        {
            "videoId": "vid-radio-1",
            "title": "Radio One",
            "artists": [{"name": "Epsilon", "id": "UCepsilon"}],
            "length": "3:10",
            "duration_seconds": 190,
        },
        {
            "videoId": "vid-radio-2",
            "title": "Radio Two",
            "artists": [{"name": "Zeta", "id": "UCzeta"}],
            "duration_seconds": 205,
        },
    ],
}

WATCH_PLAYLIST_EMPTY: dict[str, Any] = {"playlistId": "RDempty", "related": None, "tracks": []}

SONG_RELATED: list[dict[str, Any]] = [
    {
        "title": "You might also like",
        "contents": [
            {
                "videoId": "vid-related-1",
                "title": "Related One",
                "artists": [{"name": "Eta", "id": "UCeta"}],
                "duration_seconds": 240,
            }
        ],
    },
    # Non-song shelves (artists, albums) must be ignored.
    {"title": "Similar artists", "contents": [{"title": "Theta", "browseId": "UCtheta"}]},
]

SONG_RELATED_EMPTY: list[dict[str, Any]] = []

SEARCH_SONGS: list[dict[str, Any]] = [
    {
        "category": "Songs",
        "resultType": "song",
        "videoId": "vid-search-1",
        "title": "Found Song",
        "artists": [{"name": "Iota", "id": "UCiota"}],
        "album": {"name": "Third", "id": "MPREb_album3"},
        "duration": "3:01",
    },
    {"category": "Artists", "resultType": "artist", "artist": "Iota", "browseId": "UCiota"},
]

CREATE_PLAYLIST_OK = "PLcreated0001"
CREATE_PLAYLIST_REJECTED: dict[str, Any] = {"error": {"status": "INVALID_ARGUMENT"}}
ADD_ITEMS_OK: dict[str, Any] = {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}
REMOVE_ITEMS_OK: dict[str, Any] = {"status": "STATUS_SUCCEEDED"}
EDIT_PLAYLIST_OK = "STATUS_SUCCEEDED"
DELETE_PLAYLIST_OK: dict[str, Any] = {"command": {"handlePlaylistDeletionCommand": {}}}
