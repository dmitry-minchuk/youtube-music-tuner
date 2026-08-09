"""Managed playlist lifecycle and safe publishing (docs/03 s.8, docs/11 s.2)."""

from __future__ import annotations

import pytest

from app.integrations.youtube_music.errors import ParseError, Unavailable
from app.persistence.models import ManagedPlaylist, PlaylistBackup
from app.publishing.service import (
    OwnershipError,
    PlaylistNotVerified,
    cleanup_setup_artifact,
    complete_setup,
    create_setup_intent,
    marker_for,
    publish_window,
    reconcile_setup,
    require_writable,
)
from tests.fakes import FakeCatalog, SetupCatalog, snapshot

INSTANCE = "inst-1"


def make_manifest(db, desired: list[str]) -> ManagedPlaylist:
    return create_setup_intent(db, "BALANCE", INSTANCE, desired)


# -- setup lifecycle ------------------------------------------------------


def test_intent_is_persisted_before_any_external_call(db_session) -> None:
    manifest = make_manifest(db_session, ["a", "b"])
    assert manifest.status == "CREATING"
    assert manifest.playlist_id is None
    assert manifest.ownership_marker == marker_for(INSTANCE)
    assert manifest.accepted_desired_hash is not None


def test_successful_setup_reaches_active(db_session) -> None:
    desired = ["a", "b", "c"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()

    result = complete_setup(db_session, manifest, catalog, desired)

    assert result.status == "ACTIVE"
    assert manifest.playlist_id == "PLcreated"
    title, description, video_ids = catalog.created[0]
    assert title == "Tuner · Balance"
    assert marker_for(INSTANCE) in description
    assert video_ids == desired  # one call carrying the whole list


def test_id_is_registered_as_unverified_before_verification(db_session) -> None:
    """A lost verification must not orphan the playlist."""
    desired = ["a", "b"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    catalog.verify_ids = ["a", "x"]  # different content -> verification fails

    result = complete_setup(db_session, manifest, catalog, desired)

    assert result.status == "UNVERIFIED"
    assert manifest.playlist_id == "PLcreated"
    assert manifest.setup_error_code == "VERIFICATION_MISMATCH"


def test_reordered_insert_is_still_our_playlist(db_session) -> None:
    """YouTube may land a batch insert in a different order (docs/03 s.8):
    same set + marker + uniqueness is ACTIVE; publish restores the order."""
    desired = ["a", "b", "c"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    catalog.verify_ids = ["c", "a", "b"]  # same tracks, shuffled

    result = complete_setup(db_session, manifest, catalog, desired)

    assert result.status == "ACTIVE"
    assert manifest.setup_error_code is None


def test_marker_mismatch_leaves_the_manifest_unverified(db_session) -> None:
    desired = ["a"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    catalog.verify_marker = "someone else's playlist"

    result = complete_setup(db_session, manifest, catalog, desired)
    assert result.status == "UNVERIFIED"


def test_failed_create_stays_creating_for_reconciliation(db_session) -> None:
    desired = ["a"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    catalog.create_error = Unavailable("boom")

    result = complete_setup(db_session, manifest, catalog, desired)
    assert result.status == "CREATING"
    assert manifest.playlist_id is None


def test_reconciliation_adopts_an_exact_marker_match(db_session) -> None:
    from app.domain.catalog import RemotePlaylist

    desired = ["a", "b"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog(
        playlists=[
            RemotePlaylist(
                playlist_id="PLfound",
                title="Tuner · Balance",
                description=marker_for(INSTANCE),
            )
        ]
    )
    catalog.snapshots["PLfound"] = snapshot("PLfound", desired, description=marker_for(INSTANCE))

    result = reconcile_setup(db_session, manifest, catalog, desired)
    assert result.status == "ACTIVE"
    assert manifest.playlist_id == "PLfound"
    assert catalog.created == []  # no duplicate was created


def test_reconciliation_with_no_match_allows_a_safe_retry(db_session) -> None:
    manifest = make_manifest(db_session, ["a"])
    result = reconcile_setup(db_session, manifest, SetupCatalog(playlists=[]), ["a"])
    assert result.status == "CREATING"


def test_ambiguous_reconciliation_requires_cleanup(db_session) -> None:
    from app.domain.catalog import RemotePlaylist

    manifest = make_manifest(db_session, ["a"])
    duplicate = [
        RemotePlaylist(
            playlist_id=f"PL{index}", title="Tuner · Balance", description=marker_for(INSTANCE)
        )
        for index in range(2)
    ]
    result = reconcile_setup(db_session, manifest, SetupCatalog(playlists=duplicate), ["a"])
    assert result.status == "CLEANUP_REQUIRED"


# -- cleanup --------------------------------------------------------------


def test_cleanup_deletes_only_an_unverified_artifact(db_session) -> None:
    desired = ["a"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    catalog.verify_ids = ["wrong"]
    complete_setup(db_session, manifest, catalog, desired)
    assert manifest.status == "UNVERIFIED"

    assert cleanup_setup_artifact(db_session, manifest, catalog) == "DELETED"
    assert catalog.deleted == ["PLcreated"]
    assert manifest.playlist_id is None


def test_an_active_playlist_can_be_deleted_on_request(db_session) -> None:
    """It is the listener's own playlist: refusing left no way to remove one
    they did not like. The marker check is what keeps others safe."""
    desired = ["a"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    complete_setup(db_session, manifest, catalog, desired, sleep=lambda _: None)
    assert manifest.status == "ACTIVE"

    assert cleanup_setup_artifact(db_session, manifest, catalog) == "DELETED"
    assert catalog.deleted == ["PLcreated"]
    assert manifest.playlist_id is None


def test_cleanup_refuses_a_manifest_in_any_other_state(db_session) -> None:
    manifest = make_manifest(db_session, ["a"])
    manifest.status = "DELETED"
    db_session.flush()

    catalog = SetupCatalog()
    with pytest.raises(OwnershipError):
        cleanup_setup_artifact(db_session, manifest, catalog)
    assert catalog.deleted == []


def test_lost_delete_response_leaves_cleanup_required(db_session) -> None:
    desired = ["a"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    catalog.verify_ids = ["wrong"]
    complete_setup(db_session, manifest, catalog, desired)

    def failing_delete(_playlist_id: str, _marker: str) -> None:
        raise Unavailable("timeout")

    catalog.delete_managed_playlist = failing_delete  # type: ignore[method-assign]
    assert cleanup_setup_artifact(db_session, manifest, catalog) == "CLEANUP_REQUIRED"
    assert manifest.playlist_id == "PLcreated"  # not dropped on an unclear outcome


# -- write guards ---------------------------------------------------------


def test_unverified_playlist_cannot_be_published(db_session) -> None:
    manifest = make_manifest(db_session, ["a"])
    manifest.status = "UNVERIFIED"
    with pytest.raises(PlaylistNotVerified):
        require_writable(manifest)


def test_publishing_refuses_a_marker_mismatch(db_session) -> None:
    desired = ["a", "b"]
    manifest = make_manifest(db_session, desired)
    catalog = SetupCatalog()
    complete_setup(db_session, manifest, catalog, desired)

    catalog.snapshots["PLcreated"] = snapshot(
        "PLcreated", desired, description="not our marker anymore"
    )
    with pytest.raises(OwnershipError):
        publish_window(db_session, manifest, catalog, ["b", "a"])


# -- publishing -----------------------------------------------------------


def _active_manifest(db_session, initial: list[str]) -> tuple[ManagedPlaylist, SetupCatalog]:
    manifest = make_manifest(db_session, initial)
    catalog = SetupCatalog()
    complete_setup(db_session, manifest, catalog, initial)
    assert manifest.status == "ACTIVE"
    return manifest, catalog


class TrackingCatalog(SetupCatalog):
    """Applies diffs locally so publishing can be exercised end to end."""

    def apply_playlist_diff(self, plan) -> None:
        super().apply_playlist_diff(plan)
        current = list(self.snapshots[plan.playlist_id].video_ids)
        desired_positions = getattr(self, "desired_positions", {})
        for operation in plan.operations:
            if operation.kind == "REMOVE" and operation.video_id in current:
                current.remove(operation.video_id)
            elif operation.kind == "ADD":
                current.append(operation.video_id)
            elif operation.kind == "MOVE" and operation.video_id in current:
                target = desired_positions.get(operation.video_id, len(current) - 1)
                current.remove(operation.video_id)
                current.insert(min(target, len(current)), operation.video_id)
        self.snapshots[plan.playlist_id] = snapshot(
            plan.playlist_id, current, description=marker_for(INSTANCE)
        )


def test_publishing_an_unchanged_list_is_a_no_op(db_session) -> None:
    desired = ["a", "b", "c"]
    manifest, catalog = _active_manifest(db_session, desired)

    publication = publish_window(db_session, manifest, catalog, desired)
    assert publication.status == "COMPLETE"
    assert catalog.applied == []


def test_a_backup_is_stored_before_the_first_write(db_session) -> None:
    manifest, _ = _active_manifest(db_session, ["a", "b"])
    catalog = TrackingCatalog()
    catalog.snapshots["PLcreated"] = snapshot(
        "PLcreated", ["a", "b"], description=marker_for(INSTANCE)
    )
    catalog.desired_positions = {"c": 2}

    publish_window(db_session, manifest, catalog, ["a", "b", "c"])

    backups = db_session.query(PlaylistBackup).all()
    assert len(backups) == 1
    assert backups[0].items_json["videoIds"] == ["a", "b"]


def test_small_change_completes_in_one_window(db_session) -> None:
    manifest, _ = _active_manifest(db_session, ["a", "b"])
    catalog = TrackingCatalog()
    catalog.snapshots["PLcreated"] = snapshot(
        "PLcreated", ["a", "b"], description=marker_for(INSTANCE)
    )
    desired = ["a", "b", "c"]
    catalog.desired_positions = {video: index for index, video in enumerate(desired)}

    publication = publish_window(db_session, manifest, catalog, desired)
    assert publication.status == "COMPLETE"
    assert list(catalog.snapshots["PLcreated"].video_ids) == desired


def test_large_change_becomes_partial_and_keeps_its_desired_hash(db_session) -> None:
    start = [f"old{index}" for index in range(30)]
    manifest, _ = _active_manifest(db_session, start)
    catalog = TrackingCatalog()
    catalog.snapshots["PLcreated"] = snapshot("PLcreated", start, description=marker_for(INSTANCE))
    desired = [f"new{index}" for index in range(30)]
    catalog.desired_positions = {video: index for index, video in enumerate(desired)}

    first = publish_window(db_session, manifest, catalog, desired)
    assert first.status == "PARTIAL"
    assert first.remaining_item_changes > 0
    assert first.not_before is not None  # continuation waits for the next window

    original_hash = first.desired_hash
    # A newer recommendation must not move the target mid-flight.
    second = publish_window(db_session, manifest, catalog, ["completely", "different"])
    assert second.publication_id == first.publication_id
    assert second.desired_hash == original_hash


def test_partial_publication_converges_over_several_windows(db_session) -> None:
    start = [f"old{index}" for index in range(20)]
    manifest, _ = _active_manifest(db_session, start)
    catalog = TrackingCatalog()
    catalog.snapshots["PLcreated"] = snapshot("PLcreated", start, description=marker_for(INSTANCE))
    desired = [f"new{index}" for index in range(20)]
    catalog.desired_positions = {video: index for index, video in enumerate(desired)}

    for _ in range(12):
        publication = publish_window(db_session, manifest, catalog, desired)
        if publication.status == "COMPLETE":
            break

    assert publication.status == "COMPLETE"
    assert list(catalog.snapshots["PLcreated"].video_ids) == desired


def test_each_window_stays_within_the_item_change_cap(db_session) -> None:
    start = [f"old{index}" for index in range(40)]
    manifest, _ = _active_manifest(db_session, start)
    catalog = TrackingCatalog()
    catalog.snapshots["PLcreated"] = snapshot("PLcreated", start, description=marker_for(INSTANCE))
    desired = [f"new{index}" for index in range(40)]
    catalog.desired_positions = {video: index for index, video in enumerate(desired)}

    publish_window(db_session, manifest, catalog, desired)
    applied = catalog.applied[-1]
    assert applied.item_change_count <= 15


def test_remote_change_between_windows_fails_without_writing(db_session) -> None:
    start = [f"old{index}" for index in range(30)]
    manifest, _ = _active_manifest(db_session, start)
    catalog = TrackingCatalog()
    catalog.snapshots["PLcreated"] = snapshot("PLcreated", start, description=marker_for(INSTANCE))
    desired = [f"new{index}" for index in range(30)]
    catalog.desired_positions = {video: index for index, video in enumerate(desired)}

    first = publish_window(db_session, manifest, catalog, desired)
    assert first.status == "PARTIAL"

    # Someone edited the playlist by hand in between.
    tampered = [*list(catalog.snapshots["PLcreated"].video_ids), "intruder"]
    catalog.snapshots["PLcreated"] = snapshot(
        "PLcreated", tampered, description=marker_for(INSTANCE)
    )
    writes_before = len(catalog.applied)

    second = publish_window(db_session, manifest, catalog, desired)
    assert second.status == "FAILED"
    assert second.error_code == "REMOTE_CHANGED"
    assert len(catalog.applied) == writes_before


# -- verification is retried while YouTube catches up ---------------------


class SlowToAppearCatalog(SetupCatalog):
    """A just-created playlist is not readable straight away.

    The first reads fail to parse — exactly what YouTube answered when all
    three playlists ended up UNVERIFIED despite being created correctly.
    """

    def __init__(self, fail_reads: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_reads = fail_reads
        self.reads = 0

    def playlist(self, playlist_id: str):
        self.reads += 1
        if self.reads <= self.fail_reads:
            raise ParseError("playlist payload is missing a title")
        return super().playlist(playlist_id)


def test_verification_retries_until_the_playlist_is_readable(db_session) -> None:
    catalog = SlowToAppearCatalog(fail_reads=2)
    manifest = make_manifest(db_session, ["a", "b"])

    result = complete_setup(db_session, manifest, catalog, ["a", "b"], sleep=lambda _: None)

    assert catalog.reads == 3
    assert result.status == "ACTIVE"
    assert manifest.setup_error_code is None


def test_verification_gives_up_after_the_retries_and_keeps_the_id(db_session) -> None:
    catalog = SlowToAppearCatalog(fail_reads=99)
    manifest = make_manifest(db_session, ["a", "b"])

    result = complete_setup(db_session, manifest, catalog, ["a", "b"], sleep=lambda _: None)

    assert result.status == "UNVERIFIED"
    assert result.playlist_id == catalog.created_id
    assert manifest.setup_error_code == "YTM_PARSE_ERROR"


def test_verification_compares_against_the_agreed_hash(db_session) -> None:
    """Regenerating the desired list gives a different wave every time, so a
    later adopt could never match. The hash recorded at write time is the
    reference — this is what left every playlist stuck at UNVERIFIED."""
    catalog = SetupCatalog()
    manifest = make_manifest(db_session, ["a", "b", "c"])
    complete_setup(db_session, manifest, catalog, ["a", "b", "c"], sleep=lambda _: None)
    assert manifest.status == "ACTIVE"

    manifest.status = "UNVERIFIED"
    db_session.flush()

    # An empty desired list stands in for "whatever the ranker would produce
    # now": verification must still succeed off the stored hash.
    result = reconcile_setup(db_session, manifest, catalog, [])
    assert result.status == "ACTIVE"
    assert manifest.setup_error_code is None


def test_a_remotely_edited_playlist_still_fails_verification(db_session) -> None:
    catalog = SetupCatalog()
    manifest = make_manifest(db_session, ["a", "b", "c"])
    complete_setup(db_session, manifest, catalog, ["a", "b", "c"], sleep=lambda _: None)

    catalog.snapshots[catalog.created_id] = snapshot(
        catalog.created_id, ["a", "b"], description=manifest.ownership_marker
    )
    manifest.status = "UNVERIFIED"
    db_session.flush()

    result = reconcile_setup(db_session, manifest, catalog, [])
    assert result.status == "UNVERIFIED"
    assert manifest.setup_error_code == "VERIFICATION_MISMATCH"


# -- lifecycle after deletion (docs/03 s.8) ---------------------------------


def test_create_setup_intent_reuse_resets_publish_pacing(db_session) -> None:
    """A reused row is a brand-new playlist for publishing purposes.

    Without the reset, publish -> delete -> recreate left the fresh playlist
    blocked by the previous life's daily window, and Preview kept returning
    the list the listener had just deleted.
    """
    import datetime as dt

    from app.persistence.models import utcnow

    manifest = make_manifest(db_session, ["v1"])
    manifest.status = "DELETED"
    manifest.playlist_id = None
    manifest.last_published_at = utcnow()
    manifest.next_publish_after = utcnow() + dt.timedelta(hours=20)
    manifest.setup_finished_at = utcnow()
    manifest.proposed_desired_json = {"tracks": [{"videoId": "old"}]}
    db_session.flush()

    reused = create_setup_intent(db_session, "BALANCE", INSTANCE, ["v2"])

    assert reused.managed_playlist_id == manifest.managed_playlist_id
    assert reused.status == "CREATING"
    assert reused.last_published_at is None
    assert reused.next_publish_after is None
    assert reused.setup_finished_at is None
    assert reused.proposed_desired_json is None


def test_cleanup_deletes_a_creating_intent_without_remote_id(db_session) -> None:
    """An abandoned intent is pure local state; deleting it must not 403."""
    catalog = FakeCatalog()
    manifest = make_manifest(db_session, ["v1"])
    assert manifest.status == "CREATING"
    assert manifest.playlist_id is None

    status = cleanup_setup_artifact(db_session, manifest, catalog)

    assert status == "DELETED"
    assert catalog.deleted == []
