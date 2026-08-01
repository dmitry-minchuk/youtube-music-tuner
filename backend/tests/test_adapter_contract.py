"""Contract tests for the ytmusicapi adapter (docs/11 section 3).

These assert our typed results, not a copy of the external response.
"""

from __future__ import annotations

import pytest

from app.domain.catalog import CandidateSource, Rating
from app.integrations.youtube_music.adapter import YouTubeMusicAdapter, classify_exception
from app.integrations.youtube_music.errors import (
    AuthError,
    ParseError,
    RateLimited,
    RemoteChanged,
    Unavailable,
)
from app.settings import Settings
from tests.fakes import FakeYtMusicClient
from tests.fixtures import ytmusic_payloads as payloads


@pytest.fixture
def adapter_factory(tmp_path):
    def build(**overrides) -> tuple[YouTubeMusicAdapter, FakeYtMusicClient]:
        client = FakeYtMusicClient(**overrides)
        settings = Settings(data_dir=tmp_path)
        return YouTubeMusicAdapter(settings, client_factory=lambda: client), client

    return build


def test_account_is_parsed(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    account = adapter.account()
    assert account.name == "Test Listener"
    assert account.channel_handle == "@test-listener"


def test_liked_tracks_keep_missing_album_and_duration_as_none(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    tracks = {t.video_id: t for t in adapter.liked_tracks()}

    complete = tracks["vid-complete"]
    assert complete.duration_seconds == 222
    assert complete.album_title == "First Album"

    no_album = tracks["vid-no-album"]
    assert no_album.album_title is None
    assert no_album.album_id is None
    assert no_album.duration_seconds == 245  # parsed from "4:05", not invented

    no_duration = tracks["vid-no-duration"]
    assert no_duration.duration_seconds is None


def test_multiple_artists_preserve_order(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    tracks = {t.video_id: t for t in adapter.liked_tracks()}
    names = [artist.name for artist in tracks["vid-no-album"].artists]
    assert names == ["Beta", "Gamma"]


def test_localized_metadata_survives(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    tracks = {t.video_id: t for t in adapter.liked_tracks()}
    assert tracks["vid-localized"].title == "Пісня про весну"


def test_unavailable_item_is_not_playable(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    tracks = {t.video_id: t for t in adapter.liked_tracks()}
    assert tracks["vid-unavailable"].is_playable is False
    assert tracks["vid-unavailable"].is_metadata_only is True


def test_library_playlists_skip_rows_without_id(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    playlists = adapter.library_playlists()
    assert [p.playlist_id for p in playlists] == ["PLtest0001", "PLtest0002"]
    assert playlists[0].track_count == 23  # "23" string is normalised


def test_playlist_snapshot_keeps_order_and_set_video_ids(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    snapshot = adapter.playlist("PLtest0001")
    assert [item.position for item in snapshot.items] == [1, 2, 3]
    assert [item.set_video_id for item in snapshot.items] == ["set-a", "set-b", "set-c"]
    assert snapshot.video_ids == ("vid-complete", "vid-no-album", "vid-deleted")


def test_history_never_carries_duration_of_listening(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    items = adapter.history()
    assert [item.play_order for item in items] == [1, 2]
    assert items[0].period_label == "Today"
    assert not hasattr(items[0], "played_seconds")


def test_radio_returns_ranked_candidates(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    candidates = adapter.radio("vid-complete", limit=25)
    assert [c.track.video_id for c in candidates] == ["vid-radio-1", "vid-radio-2"]
    assert [c.rank for c in candidates] == [1, 2]
    assert all(c.source is CandidateSource.RADIO for c in candidates)


def test_related_uses_browse_id_from_watch_playlist(adapter_factory) -> None:
    adapter, client = adapter_factory()
    candidates = adapter.related("vid-complete")
    assert [c.track.video_id for c in candidates] == ["vid-related-1"]
    assert client.count("get_watch_playlist") == 1
    assert client.count("get_song_related") == 1


def test_related_empty_response_is_safe(adapter_factory) -> None:
    adapter, _ = adapter_factory(get_song_related=payloads.SONG_RELATED_EMPTY)
    assert adapter.related("vid-complete") == []


def test_radio_without_related_browse_id_skips_second_call(adapter_factory) -> None:
    adapter, client = adapter_factory(get_watch_playlist=payloads.WATCH_PLAYLIST_EMPTY)
    assert adapter.related("vid-complete") == []
    assert client.count("get_song_related") == 0


def test_search_filters_non_song_results(adapter_factory) -> None:
    adapter, _ = adapter_factory()
    results = adapter.search("found")
    assert [t.video_id for t in results] == ["vid-search-1"]


def test_create_playlist_returns_id(adapter_factory) -> None:
    adapter, client = adapter_factory()
    playlist_id = adapter.create_private_playlist("Tuner · Balance", "marker", ["a", "b"])
    assert playlist_id == payloads.CREATE_PLAYLIST_OK
    _, _, kwargs = next(c for c in client.calls if c[0] == "create_playlist")
    assert kwargs["privacy_status"] == "PRIVATE"
    assert kwargs["video_ids"] == ["a", "b"]


def test_create_playlist_rejection_raises(adapter_factory) -> None:
    adapter, _ = adapter_factory(create_playlist=payloads.CREATE_PLAYLIST_REJECTED)
    with pytest.raises(RemoteChanged):
        adapter.create_private_playlist("Tuner", "marker", ["a"])


def test_rate_track_maps_to_like_status(adapter_factory) -> None:
    adapter, client = adapter_factory()
    adapter.rate_track("vid-complete", Rating.DISLIKE)
    _, args, _ = next(c for c in client.calls if c[0] == "rate_song")
    assert args[0] == "vid-complete"
    assert str(args[1]) == "LikeStatus.DISLIKE"


def test_delete_requires_matching_marker(adapter_factory) -> None:
    adapter, client = adapter_factory()
    marker = "Managed by YouTube Music Tuner; instance=inst-1; schema=1"
    adapter.delete_managed_playlist("PLtest0001", marker)
    assert client.count("delete_playlist") == 1
    assert client.count("get_playlist") == 1  # fresh marker read first


def test_delete_refuses_on_marker_mismatch(adapter_factory) -> None:
    adapter, client = adapter_factory()
    with pytest.raises(RemoteChanged):
        adapter.delete_managed_playlist("PLtest0001", "instance=other")
    assert client.count("delete_playlist") == 0


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP 401 Unauthorized", AuthError),
        ("invalid_grant: token expired", AuthError),
        ("HTTP 429 Too Many Requests", RateLimited),
        ("Connection timed out", Unavailable),
        ("HTTP 503 Service Unavailable", Unavailable),
    ],
)
def test_error_classification(message: str, expected: type[Exception]) -> None:
    assert isinstance(classify_exception(RuntimeError(message)), expected)


def test_parse_failures_become_parse_error() -> None:
    assert isinstance(classify_exception(KeyError("contents")), ParseError)


def test_auth_failure_resets_client(adapter_factory) -> None:
    adapter, _ = adapter_factory(get_liked_songs=RuntimeError("HTTP 401 Unauthorized"))
    with pytest.raises(AuthError):
        adapter.liked_tracks()
    assert adapter._client is None


def test_malformed_payload_raises_parse_error(adapter_factory) -> None:
    adapter, _ = adapter_factory(get_account_info={"unexpected": True})
    with pytest.raises(ParseError):
        adapter.account()
