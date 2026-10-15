from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.catalog import (
    ArtistRef,
    Rating,
    RemoteHistoryItem,
    RemotePlaylistItem,
    RemotePlaylistSnapshot,
    Track,
)
from app.integrations.youtube_music import parsers
from app.integrations.youtube_music.errors import WriteRejected
from app.jobs.library_sync import run_library_sync
from app.persistence import repositories as repo
from app.persistence.models import (
    ApiCallLedger,
    LibraryTrackState,
    ManagedPlaylist,
    PlaylistBackup,
    PlaylistPublication,
    utcnow,
)
from app.persistence.models import Track as TrackRow
from app.recommender.wave import publishing_exclusions
from tests.fakes import FakeCatalog
from tests.test_playlist_preview_api import add, build_library

FETCHED = dt.datetime(2026, 10, 15, 12, 0)


def remote_track(video_id: str, status: Rating | None, title: str | None = None) -> Track:
    return Track(
        video_id=video_id,
        title=title or f"Track {video_id}",
        artists=(ArtistRef(artist_id="UCx", name="X"),),
        like_status=status,
    )


def test_like_status_is_read_from_playlist_and_history_items_only() -> None:
    entry = {
        "videoId": "a",
        "title": "A",
        "artists": [{"name": "X", "id": "UCx"}],
        "setVideoId": "set-a",
        "likeStatus": "DISLIKE",
    }
    snapshot = parsers.parse_playlist_snapshot(
        {"title": "P", "tracks": [entry, {**entry, "videoId": "b", "likeStatus": "BOGUS"}]},
        "PL1",
        FETCHED,
    )
    history = parsers.parse_history([{**entry, "likeStatus": "INDIFFERENT", "played": "Today"}])
    watch = parsers.parse_watch_playlist(
        {"tracks": [{**entry, "likeStatus": "INDIFFERENT"}]}, parsers.CandidateSource.RADIO, "a"
    )

    assert [item.track.like_status for item in snapshot.items] == [Rating.DISLIKE, None]
    assert history[0].track.like_status is Rating.INDIFFERENT
    assert watch[0].track.like_status is None


def _state(db: Session, video_id: str) -> LibraryTrackState | None:
    db.expire_all()
    return db.get(LibraryTrackState, video_id)


def test_remote_ratings_follow_the_rules(db_session: Session) -> None:
    for video_id in ("d", "l", "i", "unknown-local"):
        add(db_session, video_id, "Artist")
    db_session.add(LibraryTrackState(video_id="i", is_disliked=True, desired_rating="DISLIKE"))
    db_session.add(LibraryTrackState(video_id="l", is_liked=True, desired_rating="LIKE"))
    db_session.commit()
    read_started = utcnow()

    changed = repo.apply_remote_ratings(
        db_session,
        [
            remote_track("d", Rating.DISLIKE),
            remote_track("l", Rating.INDIFFERENT),
            remote_track("i", Rating.INDIFFERENT),
            remote_track("not-in-catalogue", Rating.DISLIKE),
            remote_track("unknown-local", None),
        ],
        read_started,
        may_clear_dislikes=True,
    )
    db_session.commit()

    assert sorted(changed) == ["d", "i", "not-in-catalogue"]
    disliked = _state(db_session, "d")
    assert disliked is not None and disliked.is_disliked and not disliked.is_liked
    assert disliked.rating_sync_status == "SYNCED"
    liked = _state(db_session, "l")
    assert liked is not None and liked.is_liked
    cleared = _state(db_session, "i")
    assert cleared is not None and not cleared.is_disliked
    stored = _state(db_session, "not-in-catalogue")
    assert stored is not None and stored.is_disliked


def test_a_history_read_never_clears_a_dislike(db_session: Session) -> None:
    add(db_session, "kept", "A")
    db_session.add(LibraryTrackState(video_id="kept", is_disliked=True, desired_rating="DISLIKE"))
    db_session.commit()

    changed = repo.apply_remote_ratings(
        db_session, [remote_track("kept", Rating.INDIFFERENT)], utcnow()
    )

    assert changed == []
    state = _state(db_session, "kept")
    assert state is not None and state.is_disliked


def test_a_newer_local_rating_beats_a_stale_read(db_session: Session) -> None:
    add(db_session, "pending", "A")
    add(db_session, "just-pushed", "B")
    db_session.add(
        LibraryTrackState(video_id="pending", desired_rating="LIKE", rating_sync_status="PENDING")
    )
    db_session.add(
        LibraryTrackState(
            video_id="just-pushed",
            is_liked=True,
            desired_rating="LIKE",
            remote_rating_synced_at=utcnow(),
        )
    )
    db_session.commit()

    changed = repo.apply_remote_ratings(
        db_session,
        [remote_track("pending", Rating.DISLIKE), remote_track("just-pushed", Rating.DISLIKE)],
        utcnow(),
    )

    assert changed == []


def test_liked_sync_does_not_overwrite_a_pending_local_dislike(db_session: Session) -> None:
    add(db_session, "t", "A")
    db_session.add(
        LibraryTrackState(
            video_id="t", is_disliked=True, desired_rating="DISLIKE", rating_sync_status="PENDING"
        )
    )
    db_session.commit()

    repo.mark_liked(db_session, {"t"}, "snap")

    state = _state(db_session, "t")
    assert state is not None and state.is_disliked and not state.is_liked


def test_library_sync_learns_dislikes_from_history(db_session: Session) -> None:
    add(db_session, "h", "A")
    db_session.commit()
    catalog = FakeCatalog(
        liked=[],
        history_items=[RemoteHistoryItem(track=remote_track("h", Rating.DISLIKE), play_order=1)],
    )

    run_library_sync(db_session, catalog, force=True)

    state = _state(db_session, "h")
    assert state is not None and state.is_disliked


def test_a_dislike_blocks_the_same_song_under_another_id(db_session: Session) -> None:
    add(db_session, "original", "Singer")
    add(db_session, "canonical", "Singer")
    add(db_session, "other-song", "Singer")
    db_session.get(TrackRow, "canonical").title = "  TRACK   ORIGINAL "
    db_session.get(TrackRow, "original").title = "Track original"
    db_session.add(LibraryTrackState(video_id="canonical", is_disliked=True))
    db_session.commit()

    excluded = publishing_exclusions(db_session)

    assert {"original", "canonical"} <= excluded
    assert "other-song" not in excluded


class LivePlaylist(FakeCatalog):
    def __init__(self, playlist_id: str, marker: str, items: list[Track]) -> None:
        super().__init__(liked=[])
        self.playlist_id = playlist_id
        self.marker = marker
        self.items = [(track, f"set-{index}") for index, track in enumerate(items)]
        self.counter = len(items)
        self.add_error: Exception | None = None
        self.remove_error: Exception | None = None
        self.reads = 0

    def playlist(self, playlist_id: str) -> RemotePlaylistSnapshot:
        self.reads += 1
        return RemotePlaylistSnapshot(
            playlist_id=playlist_id,
            title="Tuner · Balance",
            description=self.marker,
            items=tuple(
                RemotePlaylistItem(position=index, track=track, set_video_id=set_id)
                for index, (track, set_id) in enumerate(self.items)
            ),
            fetched_at=utcnow(),
        )

    def append_playlist_items(self, playlist_id: str, video_ids: list[str]) -> None:
        if self.add_error is not None:
            raise self.add_error
        super().append_playlist_items(playlist_id, video_ids)
        for video_id in video_ids:
            self.counter += 1
            self.items.append((remote_track(video_id, None), f"set-{self.counter}"))

    def remove_playlist_items(self, playlist_id: str, items: list[tuple[str, str]]) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        super().remove_playlist_items(playlist_id, items)
        gone = {set_id for _, set_id in items}
        self.items = [(track, set_id) for track, set_id in self.items if set_id not in gone]

    def ids(self) -> list[str]:
        return [track.video_id or "" for track, _ in self.items]


@pytest.fixture
def live(db_session: Session, fake_catalog: FakeCatalog) -> LivePlaylist:
    build_library(db_session)
    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    current = [f"like-{index}" for index in range(30)] + [f"disc-{index}" for index in range(30)]
    disliked_on_phone = {"like-0", "like-1", "disc-0", "disc-1"}
    tracks = [
        remote_track(video_id, Rating.DISLIKE if video_id in disliked_on_phone else None)
        for video_id in current
    ]
    catalog = LivePlaylist(manifest.playlist_id or "", manifest.ownership_marker, tracks)
    fake_catalog.__class__ = LivePlaylist
    fake_catalog.__dict__.update(catalog.__dict__)
    return fake_catalog  # type: ignore[return-value]


def regenerate(client: TestClient) -> dict:
    response = client.post("/api/v1/managed-playlists/BALANCE/regenerate")
    assert response.status_code == 200, response.text
    return response.json()


def test_regenerate_drops_tracks_disliked_on_youtube(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    body = regenerate(authed_client)

    assert body["status"] == "COMPLETE"
    assert body["ratingsApplied"] == 4
    posted = live.ids()
    assert not {"like-0", "like-1", "disc-0", "disc-1"} & set(posted)
    assert [track["videoId"] for track in body["tracks"]] == posted
    assert body["removedDisliked"] == 4
    state = _state(db_session, "like-0")
    assert state is not None and state.is_disliked and not state.is_liked


def test_regenerate_adds_before_it_removes_and_keeps_a_backup(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    before = live.ids()

    regenerate(authed_client)

    assert len(live.appended) == 1 and len(live.removed) == 1
    assert [video_id for video_id, _ in live.removed[0][1]] == before
    db_session.expire_all()
    backup = db_session.scalar(select(PlaylistBackup))
    assert backup is not None and backup.items_json["videoIds"] == before


def test_a_failed_add_writes_nothing_and_keeps_learned_dislikes(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    before = live.ids()
    live.add_error = WriteRejected("rejected")

    body = regenerate(authed_client)

    assert body["status"] == "FAILED" and body["errorCode"] == "ADD_FAILED"
    assert live.ids() == before
    assert live.removed == []
    state = _state(db_session, "disc-0")
    assert state is not None and state.is_disliked


def test_a_failed_remove_never_empties_the_playlist(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    live.remove_error = WriteRejected("rejected")

    body = regenerate(authed_client)

    assert body["errorCode"] == "REMOVE_FAILED_AFTER_ADD"
    assert len(live.ids()) > 60


def test_regenerate_supersedes_an_open_partial(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    manifest.setup_started_at = utcnow() - dt.timedelta(days=3)
    db_session.add(
        PlaylistPublication(
            publication_id="old-partial",
            managed_playlist_id=manifest.managed_playlist_id,
            status="PARTIAL",
            created_at=utcnow() - dt.timedelta(hours=30),
            desired_snapshot_json={"videoIds": ["like-0"]},
            desired_hash="old",
        )
    )
    db_session.commit()

    regenerate(authed_client)

    db_session.expire_all()
    partial = db_session.get(PlaylistPublication, "old-partial")
    assert partial is not None
    assert (partial.status, partial.error_code) == ("FAILED", "SUPERSEDED_BY_REGENERATE")


def test_regenerate_is_refused_without_budget_or_while_another_write_runs(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    db_session.add(
        PlaylistPublication(
            publication_id="running",
            managed_playlist_id=manifest.managed_playlist_id,
            status="WRITING",
            created_at=utcnow(),
            desired_hash="running",
        )
    )
    db_session.commit()

    busy = authed_client.post("/api/v1/managed-playlists/BALANCE/regenerate")
    assert busy.status_code == 409

    db_session.get(PlaylistPublication, "running").status = "COMPLETE"
    now = utcnow()
    for _ in range(12):
        db_session.add(
            ApiCallLedger(
                provider="ytmusic",
                operation="get_playlist",
                request_fingerprint="get_playlist",
                playlist_id=manifest.playlist_id,
                is_mutation=False,
                counts_against_automatic_budget=True,
                started_at=now,
                finished_at=now,
                duration_ms=1,
                outcome="SUCCESS",
            )
        )
    db_session.commit()

    exhausted = authed_client.post("/api/v1/managed-playlists/BALANCE/regenerate")
    assert exhausted.status_code == 429
    assert live.reads == 0


def test_regenerate_is_only_for_active_playlists(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    manifest.status = "UNVERIFIED"
    db_session.commit()

    response = authed_client.post("/api/v1/managed-playlists/BALANCE/regenerate")

    assert response.status_code == 409
    assert live.appended == []


def test_a_stored_preview_with_a_newly_disliked_track_is_rebuilt(
    authed_client: TestClient, db_session: Session
) -> None:
    build_library(db_session)
    first = authed_client.post("/api/v1/managed-playlists/BALANCE/plan", json={}).json()
    victim = first["tracks"][0]["videoId"]
    existing = db_session.get(LibraryTrackState, victim)
    if existing is None:
        db_session.add(LibraryTrackState(video_id=victim, is_disliked=True))
    else:
        existing.is_disliked = True
        existing.is_liked = False
    db_session.commit()

    again = authed_client.post("/api/v1/managed-playlists/BALANCE/plan", json={}).json()

    assert victim not in [track["videoId"] for track in again["tracks"]]


def test_a_partial_whose_plan_holds_a_new_dislike_is_cancelled(
    db_session: Session, live: LivePlaylist
) -> None:
    from app.publishing.service import publish_window

    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    live.items = [(replace(track, like_status=None), set_id) for track, set_id in live.items]
    frozen = list(reversed(live.ids()))
    db_session.add(
        PlaylistPublication(
            publication_id="partial",
            managed_playlist_id=manifest.managed_playlist_id,
            status="PARTIAL",
            created_at=utcnow(),
            desired_snapshot_json={"videoIds": frozen},
            desired_hash="partial",
        )
    )
    db_session.commit()
    live.items[0] = (remote_track(live.items[0][0].video_id or "", Rating.DISLIKE), "set-0")

    publication = publish_window(db_session, manifest, live, live.ids())

    assert publication.publication_id == "partial"
    assert (publication.status, publication.error_code) == ("FAILED", "DESIRED_INVALIDATED")
    assert live.applied == []


def test_composition_match_tolerates_order_and_a_tenth_of_substitutions() -> None:
    from app.publishing.service import matches_composition

    desired = [f"v{index}" for index in range(20)]
    assert matches_composition(list(reversed(desired)), desired)
    assert matches_composition(["x1", "x2", *desired[2:]], desired)
    assert not matches_composition(["x1", "x2", "x3", *desired[3:]], desired)
    assert matches_composition(["x1", *desired[2:]], desired)
    assert not matches_composition(desired[3:], desired)
    assert not matches_composition([desired[0], *desired[:-1]], desired)


def test_a_dislike_in_another_tuner_playlist_is_never_written(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    sibling = ManagedPlaylist(
        managed_playlist_id="mp-discovery",
        playlist_id="PLsibling",
        kind="DISCOVERY",
        instance_id="inst-1",
        ownership_marker=live.marker,
        temperature=80,
        status="ACTIVE",
    )
    db_session.add(sibling)
    db_session.commit()
    elsewhere = [f"disc-{index}" for index in range(100, 140)]
    sibling_items = [remote_track(video_id, Rating.DISLIKE) for video_id in elsewhere]
    own = live.playlist

    def reads(playlist_id: str) -> RemotePlaylistSnapshot:
        if playlist_id == "PLsibling":
            return RemotePlaylistSnapshot(
                playlist_id=playlist_id,
                title="Tuner · Discovery",
                description=live.marker,
                items=tuple(
                    RemotePlaylistItem(position=index, track=track, set_video_id=f"s{index}")
                    for index, track in enumerate(sibling_items)
                ),
                fetched_at=utcnow(),
            )
        return own(playlist_id)

    live.playlist = reads  # type: ignore[method-assign]

    body = regenerate(authed_client)

    assert body["status"] == "COMPLETE"
    assert not set(elsewhere) & set(live.ids())


def test_a_track_found_disliked_by_the_verification_read_is_removed(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    surprise: set[str] = set()
    original_append = live.append_playlist_items

    def append_and_dislike_one(playlist_id: str, video_ids: list[str]) -> None:
        original_append(playlist_id, video_ids)
        target = video_ids[3]
        surprise.add(target)
        live.items = [
            (remote_track(track.video_id or "", Rating.DISLIKE), set_id)
            if track.video_id == target
            else (track, set_id)
            for track, set_id in live.items
        ]

    live.append_playlist_items = append_and_dislike_one  # type: ignore[method-assign]

    body = regenerate(authed_client)

    assert body["status"] == "COMPLETE"
    assert body["removedAfterWrite"] == 1
    assert not surprise & set(live.ids())
    assert not surprise & {track["videoId"] for track in body["tracks"]}


def test_a_dislike_on_a_canonical_id_blocks_its_twin(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    twin_of = "like-40"
    canonical = Track(
        video_id="canonical-40",
        title=f"Track {twin_of}",
        artists=(ArtistRef(artist_id="UCFav40", name="Fav40"),),
        like_status=Rating.DISLIKE,
    )
    live.items.append((canonical, "set-canonical"))

    body = regenerate(authed_client)

    assert body["status"] == "COMPLETE"
    assert twin_of not in live.ids()


def test_nothing_is_written_when_the_list_fails_the_gates(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    for state in db_session.scalars(select(LibraryTrackState)).all():
        state.is_liked = False
        state.is_disliked = True
    db_session.commit()

    body = regenerate(authed_client)

    assert body["status"] == "SKIPPED_QUALITY"
    assert live.appended == [] and live.removed == []


def test_a_leaked_excluded_track_stops_the_write(db_session: Session, live: LivePlaylist) -> None:
    from app.publishing.desired_list import DesiredListResult
    from app.publishing.service import regenerate_contents

    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    db_session.add(LibraryTrackState(video_id="disc-200", is_disliked=True))
    db_session.commit()
    leaked = DesiredListResult(
        kind="BALANCE",
        passed=True,
        video_ids=("disc-200", *[f"disc-{index}" for index in range(201, 230)]),
        configured_target_size=60,
        effective_target_size=30,
        minimum_publish_size=25,
        required_familiar=0,
        available_familiar=0,
        required_discovery=0,
        available_discovery=0,
    )

    outcome = regenerate_contents(db_session, manifest, live, lambda: leaked)

    assert outcome.status == "SKIPPED_QUALITY"
    assert live.appended == []


def test_a_verification_mismatch_and_a_failed_read_are_reported(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    original_append = live.append_playlist_items

    def append_half(playlist_id: str, video_ids: list[str]) -> None:
        original_append(playlist_id, video_ids[: len(video_ids) // 2])

    live.append_playlist_items = append_half  # type: ignore[method-assign]
    mismatch = regenerate(authed_client)
    assert (mismatch["status"], mismatch["errorCode"]) == ("FAILED", "VERIFICATION_MISMATCH")

    listing = authed_client.get("/api/v1/playlists").json()["tunerPlaylists"]
    balance = next(row for row in listing if row["kind"] == "BALANCE")
    assert balance["lastWriteStatus"] == "FAILED"
    assert balance["lastWriteErrorCode"] == "VERIFICATION_MISMATCH"


def test_an_unreadable_playlist_after_the_write_is_reported(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    from app.integrations.youtube_music.errors import Unavailable

    original_read = live.playlist
    original_append = live.append_playlist_items
    written = {"done": False}

    def append(playlist_id: str, video_ids: list[str]) -> None:
        original_append(playlist_id, video_ids)
        written["done"] = True

    def read(playlist_id: str) -> RemotePlaylistSnapshot:
        if written["done"]:
            raise Unavailable("down")
        return original_read(playlist_id)

    live.append_playlist_items = append  # type: ignore[method-assign]
    live.playlist = read  # type: ignore[method-assign]

    body = regenerate(authed_client)

    assert (body["status"], body["errorCode"]) == ("FAILED", "VERIFY_READ_FAILED")


def test_the_new_list_is_added_before_the_old_items_are_removed(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    calls: list[str] = []
    original_append = live.append_playlist_items
    original_remove = live.remove_playlist_items

    def append(playlist_id: str, video_ids: list[str]) -> None:
        calls.append("add")
        original_append(playlist_id, video_ids)

    def remove(playlist_id: str, items: list[tuple[str, str]]) -> None:
        calls.append("remove")
        original_remove(playlist_id, items)

    live.append_playlist_items = append  # type: ignore[method-assign]
    live.remove_playlist_items = remove  # type: ignore[method-assign]

    regenerate(authed_client)

    assert calls[:2] == ["add", "remove"]


def test_the_global_playlist_budget_is_respected(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    now = utcnow()
    for index in range(46):
        db_session.add(
            ApiCallLedger(
                provider="ytmusic",
                operation="get_playlist",
                request_fingerprint="get_playlist",
                playlist_id=f"PLother{index}",
                is_mutation=False,
                counts_against_automatic_budget=True,
                started_at=now,
                finished_at=now,
                duration_ms=1,
                outcome="SUCCESS",
            )
        )
    db_session.commit()

    response = authed_client.post("/api/v1/managed-playlists/BALANCE/regenerate")

    assert response.status_code == 429
    assert live.appended == []


def test_the_budget_constant_covers_every_request_of_one_press() -> None:
    from app.integrations.youtube_music.ledger import REGENERATE_REQUESTS
    from app.publishing.service import REGENERATE_VERIFY_DELAYS

    fresh_read, add, remove, cleanup = 1, 1, 1, 1
    assert (
        fresh_read + add + remove + len(REGENERATE_VERIFY_DELAYS) + cleanup == REGENERATE_REQUESTS
    )


def test_an_unanswered_add_is_reported_as_unknown(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    from app.integrations.youtube_music.errors import Unavailable

    live.add_error = Unavailable("timeout")

    body = regenerate(authed_client)

    assert (body["status"], body["errorCode"]) == ("FAILED", "ADD_OUTCOME_UNKNOWN")
    assert live.removed == []


def test_a_refused_cleanup_is_reported(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    original_append = live.append_playlist_items
    original_remove = live.remove_playlist_items
    removes = {"count": 0}

    def append_and_dislike_one(playlist_id: str, video_ids: list[str]) -> None:
        original_append(playlist_id, video_ids)
        live.items[-1] = (remote_track(live.items[-1][0].video_id or "", Rating.DISLIKE), "x")

    def remove(playlist_id: str, items: list[tuple[str, str]]) -> None:
        removes["count"] += 1
        if removes["count"] > 1:
            raise WriteRejected("rejected")
        original_remove(playlist_id, items)

    live.append_playlist_items = append_and_dislike_one  # type: ignore[method-assign]
    live.remove_playlist_items = remove  # type: ignore[method-assign]

    body = regenerate(authed_client)

    assert (body["status"], body["errorCode"]) == ("FAILED", "CLEANUP_FAILED")


def test_a_rate_limited_sibling_read_stops_before_any_write(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    from app.integrations.youtube_music.errors import RateLimited, Unavailable

    db_session.add(
        ManagedPlaylist(
            managed_playlist_id="mp-familiar",
            playlist_id="PLsibling",
            kind="FAMILIAR",
            instance_id="inst-1",
            ownership_marker=live.marker,
            temperature=20,
            status="ACTIVE",
        )
    )
    db_session.commit()
    own = live.playlist
    failure: dict[str, Exception] = {"error": Unavailable("down")}

    def reads(playlist_id: str) -> RemotePlaylistSnapshot:
        if playlist_id == "PLsibling":
            raise failure["error"]
        return own(playlist_id)

    live.playlist = reads  # type: ignore[method-assign]

    skipped = regenerate(authed_client)
    assert skipped["status"] == "COMPLETE"

    failure["error"] = RateLimited("429")
    appended_before = len(live.appended)
    refused = authed_client.post("/api/v1/managed-playlists/BALANCE/regenerate")
    assert refused.status_code >= 400
    assert len(live.appended) == appended_before


def test_legacy_publish_waits_for_a_running_write(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    db_session.add(
        PlaylistPublication(
            publication_id="running",
            managed_playlist_id=manifest.managed_playlist_id,
            status="VERIFYING",
            created_at=utcnow(),
            desired_hash="running",
        )
    )
    db_session.commit()

    response = authed_client.post("/api/v1/managed-playlists/BALANCE/publish")

    assert response.status_code == 409


def test_canonicalised_and_dropped_ids_still_count_as_written(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    original_append = live.append_playlist_items

    def append_like_youtube(playlist_id: str, video_ids: list[str]) -> None:
        stored = [f"canon-{video_ids[0]}", f"canon-{video_ids[1]}", *video_ids[3:]]
        original_append(playlist_id, stored)

    live.append_playlist_items = append_like_youtube  # type: ignore[method-assign]

    body = regenerate(authed_client)

    assert body["status"] == "COMPLETE"
    assert [track["videoId"] for track in body["tracks"]] == live.ids()
    db_session.expire_all()
    manifest = db_session.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == "BALANCE"))
    assert manifest is not None
    from app.publishing.service import desired_hash

    assert manifest.accepted_desired_hash == desired_hash(live.ids())


def test_composition_tolerance_edges() -> None:
    from app.publishing.service import matches_composition

    twenty = [f"v{index}" for index in range(20)]
    assert not matches_composition([*twenty, "a", "b", "c"], twenty)
    nine = [f"n{index}" for index in range(9)]
    assert matches_composition(list(reversed(nine)), nine)
    assert not matches_composition(nine[1:], nine)
    assert not matches_composition([], twenty)
    assert not matches_composition(twenty, [])


def test_a_dislike_from_the_listening_history_is_never_written(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    played_elsewhere = [f"disc-{index}" for index in range(140, 240)]
    live.history_items = [
        RemoteHistoryItem(track=remote_track(video_id, Rating.DISLIKE), play_order=order)
        for order, video_id in enumerate(played_elsewhere)
    ]

    body = regenerate(authed_client)

    assert body["status"] == "COMPLETE"
    assert body["ratingsApplied"] >= len(played_elsewhere)
    assert not set(played_elsewhere) & set(live.ids())
    assert body["removedAfterWrite"] == 0


def test_a_rate_limited_history_read_stops_before_any_write(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    from app.integrations.youtube_music.errors import RateLimited

    def refuse() -> list:
        raise RateLimited("429")

    live.history = refuse  # type: ignore[method-assign]

    response = authed_client.post("/api/v1/managed-playlists/BALANCE/regenerate")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "LOCAL_BUDGET_EXCEEDED"
    assert live.appended == []


def test_an_unavailable_history_is_skipped(
    authed_client: TestClient, db_session: Session, live: LivePlaylist
) -> None:
    from app.integrations.youtube_music.errors import Unavailable

    def down() -> list:
        raise Unavailable("down")

    live.history = down  # type: ignore[method-assign]

    body = regenerate(authed_client)

    assert body["status"] == "COMPLETE"
    assert body["ratingsApplied"] == 4
