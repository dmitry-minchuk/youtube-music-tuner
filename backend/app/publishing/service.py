"""Managed playlist setup and publishing (docs/03 section 8, docs/10 s.5).

Ownership is proven twice — the local manifest and the remote marker — and
the setup intent is written before the external create, so a lost response
never leaves an unreachable playlist behind.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.catalog import PlaylistDiffPlan, RemotePlaylistSnapshot
from app.integrations.youtube_music.errors import IntegrationError
from app.integrations.youtube_music.port import MusicCatalogPort
from app.persistence.models import (
    ManagedPlaylist,
    PlaylistBackup,
    PlaylistPublication,
    utcnow,
)
from app.publishing.planner import (
    PlanSlice,
    RemoteItem,
    build_plan,
    plan_window,
    with_refreshed_set_video_ids,
)
from app.publishing.quality_gates import QUALITY_GATE_VERSION

logger = logging.getLogger(__name__)

MARKER_TEMPLATE = "Managed by YouTube Music Tuner; instance={instance}; schema=1"
PLAYLIST_KINDS = {"FAMILIAR": 20, "BALANCE": 50, "DISCOVERY": 80}
PLAYLIST_TITLES = {
    "FAMILIAR": "Tuner · Familiar",
    "BALANCE": "Tuner · Balance",
    "DISCOVERY": "Tuner · Discovery",
}
# Verification retries. The first read happens immediately; the two that
# follow wait, because a just-created playlist needs a moment to become
# readable. Each retry is one playlist request against the window budget.
VERIFY_BACKOFF_SECONDS: tuple[float, ...] = (0.0, 2.0, 5.0)

# In-place id canonicalisation is accepted for up to a tenth of the list
# (none at all for lists shorter than the divisor) — docs/03 s.8.
SUBSTITUTION_TOLERANCE_DIVISOR = 10


def matches_up_to_substitutions(actual: list[str], expected: list[str]) -> bool:
    """Equal lists, allowing YouTube's in-place id canonicalisation.

    An inserted track can be stored under the canonical id of the same song,
    at the same position (docs/03 s.8). Anything beyond a tenth of the list
    swapped in place — or any structural difference — is a real mismatch.
    """
    if actual == expected:
        return True
    if len(actual) != len(expected):
        return False
    swapped = sum(1 for got, want in zip(actual, expected, strict=True) if got != want)
    return 0 < swapped <= len(expected) // SUBSTITUTION_TOLERANCE_DIVISOR


PUBLISH_WINDOW = dt.timedelta(hours=24)
MAX_BACKUPS = 30


class OwnershipError(Exception):
    """Raised whenever a write would touch something Tuner does not own."""


class PlaylistNotVerified(Exception):
    pass


def marker_for(instance_id: str) -> str:
    return MARKER_TEMPLATE.format(instance=instance_id)


def desired_hash(video_ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(video_ids, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SetupResult:
    managed_playlist_id: str
    playlist_id: str | None
    status: str
    effective_target_size: int
    error_code: str | None = None


def create_setup_intent(
    db: Session,
    kind: str,
    instance_id: str,
    desired: list[str],
    *,
    configured_target_size: int = 60,
) -> ManagedPlaylist:
    """Persist CREATING before any external call (docs/03 section 8)."""
    if kind not in PLAYLIST_KINDS:
        raise ValueError(f"unknown managed playlist kind: {kind}")

    existing = db.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == kind))
    if existing is not None and existing.status in {"ACTIVE", "UNVERIFIED", "CLEANUP_REQUIRED"}:
        return existing

    manifest = existing or ManagedPlaylist(
        managed_playlist_id=str(uuid.uuid4()),
        kind=kind,
        instance_id=instance_id,
        ownership_marker=marker_for(instance_id),
        temperature=PLAYLIST_KINDS[kind],
    )
    manifest.instance_id = instance_id
    manifest.ownership_marker = marker_for(instance_id)
    manifest.configured_target_size = configured_target_size
    manifest.accepted_desired_hash = desired_hash(desired)
    manifest.status = "CREATING"
    manifest.setup_started_at = utcnow()
    manifest.setup_error_code = None
    # A reused row (DELETED tombstone or an abandoned CREATING intent) is a
    # brand-new playlist from here on: the previous life's publish pacing
    # would block the first publish for a day, and its remembered preview
    # would republish the very list the listener just deleted.
    manifest.setup_finished_at = None
    manifest.last_published_at = None
    manifest.next_publish_after = None
    manifest.proposed_desired_json = None
    db.add(manifest)
    db.flush()
    return manifest


def complete_setup(
    db: Session,
    manifest: ManagedPlaylist,
    catalog: MusicCatalogPort,
    desired: list[str],
    *,
    sleep=time.sleep,
) -> SetupResult:
    """Create the playlist, register the id immediately, then verify."""
    title = PLAYLIST_TITLES[manifest.kind]
    description = manifest.ownership_marker

    try:
        playlist_id = catalog.create_private_playlist(title, description, desired)
    except IntegrationError as exc:
        manifest.setup_error_code = exc.code
        db.flush()
        # Stay CREATING: reconciliation decides later whether it went through.
        return SetupResult(
            manifest.managed_playlist_id, None, "CREATING", len(desired), error_code=exc.code
        )

    # Register before verifying so a crash cannot orphan the playlist. The
    # commit matters: a flush alone keeps the row inside a transaction that a
    # crash would roll back, and it also keeps SQLite's single write lock held
    # across the verification calls that follow.
    manifest.playlist_id = playlist_id
    manifest.status = "UNVERIFIED"
    db.flush()
    db.commit()

    return verify_setup(db, manifest, catalog, desired, sleep=sleep)


def verify_setup(
    db: Session,
    manifest: ManagedPlaylist,
    catalog: MusicCatalogPort,
    desired: list[str],
    *,
    sleep=time.sleep,
) -> SetupResult:
    if manifest.playlist_id is None:
        return SetupResult(manifest.managed_playlist_id, None, manifest.status, len(desired))

    # A freshly created playlist is not readable straight away: YouTube answers
    # with a partial payload that fails to parse, or with an order that has not
    # settled yet. One attempt left all three playlists UNVERIFIED even though
    # they had been created correctly, so the read is retried briefly.
    snapshot = None
    last_error: str | None = None
    for attempt, delay in enumerate(VERIFY_BACKOFF_SECONDS):
        if attempt:
            sleep(delay)
        try:
            snapshot = catalog.playlist(manifest.playlist_id)
        except IntegrationError as exc:
            last_error = exc.code
            continue
        if snapshot.description and manifest.ownership_marker in snapshot.description:
            break

    if snapshot is None:
        manifest.setup_error_code = last_error or "YTM_PARSE_ERROR"
        db.flush()
        return SetupResult(
            manifest.managed_playlist_id,
            manifest.playlist_id,
            manifest.status,
            len(desired),
            error_code=manifest.setup_error_code,
        )

    matches_marker = bool(
        snapshot.description and manifest.ownership_marker in snapshot.description
    )
    # Compare against the list we agreed to write, recorded as a hash on the
    # manifest — not against a freshly generated one. Regenerating gives a
    # different wave every time, so verification could never succeed after the
    # fact and every adopt attempt reported VERIFICATION_MISMATCH.
    remote_hash = desired_hash(list(snapshot.video_ids))
    if manifest.accepted_desired_hash:
        matches_order = remote_hash == manifest.accepted_desired_hash
    else:
        matches_order = list(snapshot.video_ids) == desired
    unique = len(set(snapshot.video_ids)) == len(snapshot.video_ids)
    # YouTube does not guarantee batch-insert order: the same sixty tracks may
    # land shuffled, reproducibly (docs/03 s.8). Marker + uniqueness + the
    # exact same set is unambiguously our playlist; the first publish restores
    # the order deterministically with its move operations. Only the setup
    # path can check this — reconcile carries no reference list.
    matches_set = bool(desired) and sorted(snapshot.video_ids) == sorted(desired)

    # YouTube canonicalises ids on insert: a track can land as another id of
    # the same song, in place, with every other position untouched (observed
    # live, docs/03 s.8). A list we just wrote, marker-matched, aligned at
    # every position but a few in-place swaps is unambiguously ours.
    substituted_ok = (
        not matches_order
        and bool(desired)
        and matches_up_to_substitutions(list(snapshot.video_ids), desired)
    )

    if matches_marker and unique and (matches_order or matches_set or substituted_ok):
        manifest.status = "ACTIVE"
        manifest.setup_finished_at = utcnow()
        manifest.setup_error_code = None
        if not matches_order:
            # Adopt what YouTube actually stored, so later verifications and
            # publish diffs do not keep fighting the canonical ids.
            manifest.accepted_desired_hash = remote_hash
    else:
        manifest.setup_error_code = "VERIFICATION_MISMATCH"
        # Never "top up" or reshuffle automatically: leave it for a human.
    db.flush()

    return SetupResult(
        manifest.managed_playlist_id,
        manifest.playlist_id,
        manifest.status,
        len(desired),
        error_code=manifest.setup_error_code,
    )


def reconcile_setup(
    db: Session,
    manifest: ManagedPlaylist,
    catalog: MusicCatalogPort,
    desired: list[str],
) -> SetupResult:
    """After a crash: adopt an exact marker match, never create a duplicate."""
    if manifest.status == "ACTIVE":
        return SetupResult(
            manifest.managed_playlist_id, manifest.playlist_id, "ACTIVE", len(desired)
        )

    if manifest.playlist_id is not None:
        return verify_setup(db, manifest, catalog, desired)

    expected_title = PLAYLIST_TITLES[manifest.kind]
    try:
        candidates = [
            playlist
            for playlist in catalog.library_playlists()
            if playlist.title == expected_title
            and playlist.description
            and manifest.ownership_marker in playlist.description
        ]
    except IntegrationError as exc:
        manifest.setup_error_code = exc.code
        db.flush()
        return SetupResult(
            manifest.managed_playlist_id, None, manifest.status, len(desired), error_code=exc.code
        )

    if len(candidates) == 1:
        manifest.playlist_id = candidates[0].playlist_id
        manifest.status = "UNVERIFIED"
        db.flush()
        return verify_setup(db, manifest, catalog, desired)

    if len(candidates) > 1:
        manifest.status = "CLEANUP_REQUIRED"
        manifest.setup_error_code = "AMBIGUOUS_MATCH"
        db.flush()
        return SetupResult(
            manifest.managed_playlist_id,
            None,
            manifest.status,
            len(desired),
            error_code="AMBIGUOUS_MATCH",
        )

    # Nothing was created: safe to retry the create explicitly.
    manifest.status = "CREATING"
    db.flush()
    return SetupResult(manifest.managed_playlist_id, None, "CREATING", len(desired))


DELETABLE_STATUSES = frozenset({"CREATING", "UNVERIFIED", "CLEANUP_REQUIRED", "ACTIVE"})


def cleanup_setup_artifact(
    db: Session, manifest: ManagedPlaylist, catalog: MusicCatalogPort
) -> str:
    """Delete one of our own playlists, after a fresh marker check.

    ACTIVE is deletable too: it is the listener's own playlist and the marker
    check still guarantees Tuner never touches anything it did not create.
    Refusing meant a playlist you disliked could not be removed from the app
    at all. CREATING is deletable as well (docs/08 s.7: any status): an
    abandoned intent without a remote id is pure local state, and one with an
    id still passes the same marker check as everything else.
    """
    if manifest.status not in DELETABLE_STATUSES:
        raise OwnershipError("only a Tuner-owned playlist may be deleted")
    if manifest.playlist_id is None:
        manifest.status = "DELETED"
        db.flush()
        return "DELETED"

    try:
        catalog.delete_managed_playlist(manifest.playlist_id, manifest.ownership_marker)
    except IntegrationError as exc:
        manifest.status = "CLEANUP_REQUIRED"
        manifest.setup_error_code = exc.code
        db.flush()
        # Ambiguous outcome: no blind retry, the next attempt reads first.
        return "CLEANUP_REQUIRED"

    manifest.status = "DELETED"
    manifest.playlist_id = None
    manifest.setup_finished_at = utcnow()
    db.flush()
    return "DELETED"


def require_writable(manifest: ManagedPlaylist) -> None:
    if manifest.status == "ACTIVE":
        return
    if manifest.status in {"CREATING", "UNVERIFIED", "CLEANUP_REQUIRED"}:
        raise PlaylistNotVerified(f"playlist is {manifest.status}")
    raise OwnershipError("playlist is not writable")


def snapshot_to_items(snapshot: RemotePlaylistSnapshot) -> list[RemoteItem]:
    return [
        RemoteItem(video_id=item.track.video_id, set_video_id=item.set_video_id)
        for item in snapshot.items
        if item.track.video_id and item.set_video_id
    ]


def store_backup(
    db: Session, manifest: ManagedPlaylist, snapshot: RemotePlaylistSnapshot, publication_id: str
) -> PlaylistBackup:
    backup = PlaylistBackup(
        backup_id=str(uuid.uuid4()),
        managed_playlist_id=manifest.managed_playlist_id,
        publication_id=publication_id,
        items_json={"videoIds": list(snapshot.video_ids)},
        content_hash=desired_hash(list(snapshot.video_ids)),
    )
    db.add(backup)
    db.flush()
    _prune_backups(db, manifest)
    return backup


def _prune_backups(db: Session, manifest: ManagedPlaylist) -> None:
    rows = db.scalars(
        select(PlaylistBackup)
        .where(PlaylistBackup.managed_playlist_id == manifest.managed_playlist_id)
        .order_by(PlaylistBackup.created_at.desc())
    ).all()
    for stale in rows[MAX_BACKUPS:]:
        db.delete(stale)


def active_partial(db: Session, manifest: ManagedPlaylist) -> PlaylistPublication | None:
    partial = db.scalar(
        select(PlaylistPublication)
        .where(
            PlaylistPublication.managed_playlist_id == manifest.managed_playlist_id,
            PlaylistPublication.status == "PARTIAL",
        )
        .order_by(PlaylistPublication.created_at.desc())
        .limit(1)
    )
    if partial is None:
        return None
    # A continuation carries the snapshot and expected hash of a remote list
    # that may no longer exist: the manifest row is reused across delete and
    # recreate, so a PARTIAL from before the current setup began belongs to a
    # previous playlist. Continuing it is a guaranteed REMOTE_CHANGED
    # (docs/03 s.8) — retire it and let a fresh cycle start.
    if manifest.setup_started_at is not None and partial.created_at < manifest.setup_started_at:
        partial.status = "FAILED"
        partial.error_code = "SUPERSEDED_BY_SETUP"
        db.flush()
        return None
    return partial


def publish_window(
    db: Session,
    manifest: ManagedPlaylist,
    catalog: MusicCatalogPort,
    desired: list[str],
    *,
    target_generation_id: str | None = None,
    now: dt.datetime | None = None,
) -> PlaylistPublication:
    """Apply one bounded window of the diff and verify the outcome."""
    now = now or utcnow()
    require_writable(manifest)
    assert manifest.playlist_id is not None

    partial = active_partial(db, manifest)
    if partial is not None:
        # A continuation keeps the immutable desired snapshot it started with.
        desired = list(partial.desired_snapshot_json.get("videoIds", desired))
        publication = partial
    else:
        publication = PlaylistPublication(
            publication_id=str(uuid.uuid4()),
            managed_playlist_id=manifest.managed_playlist_id,
            status="PLANNED",
            target_generation_id=target_generation_id,
            quality_gate_version=QUALITY_GATE_VERSION,
            configured_target_size=manifest.configured_target_size,
            effective_target_size=len(desired),
            desired_snapshot_json={"videoIds": desired},
            desired_hash=desired_hash(desired),
            created_at=now,
        )
        db.add(publication)
        db.flush()

    snapshot = catalog.playlist(manifest.playlist_id)
    if not (snapshot.description and manifest.ownership_marker in snapshot.description):
        publication.status = "FAILED"
        publication.error_code = "MARKER_MISMATCH"
        db.flush()
        raise OwnershipError("remote ownership marker does not match")

    remote_items = snapshot_to_items(snapshot)
    current_hash = desired_hash([item.video_id for item in remote_items])

    if (
        publication.expected_intermediate_hash
        and publication.expected_intermediate_hash != current_hash
    ):
        publication.status = "FAILED"
        publication.error_code = "REMOTE_CHANGED"
        db.flush()
        return publication

    if publication.remote_before_json.get("videoIds") is None:
        publication.remote_before_json = {"videoIds": [i.video_id for i in remote_items]}
        store_backup(db, manifest, snapshot, publication.publication_id)

    plan_slice = plan_window(remote_items, desired)
    if not plan_slice.operations:
        publication.status = "COMPLETE"
        publication.verification_hash = current_hash
        publication.remaining_item_changes = 0
        publication.remaining_estimated_requests = 0
        manifest.last_published_at = now
        manifest.next_publish_after = now + PUBLISH_WINDOW
        db.flush()
        return publication

    publication.status = "WRITING"
    db.flush()

    plan = with_refreshed_set_video_ids(_plan_from_slice(manifest, plan_slice), remote_items)
    try:
        catalog.apply_playlist_diff(plan)
    except IntegrationError as exc:
        publication.status = "FAILED"
        publication.error_code = exc.code
        db.flush()
        return publication

    publication.status = "VERIFYING"
    publication.applied_operations_json = {
        "operations": [{"kind": op.kind, "videoId": op.video_id} for op in plan_slice.operations]
    }
    db.flush()

    verification = catalog.playlist(manifest.playlist_id)
    verified_ids = list(verification.video_ids)
    verified_hash = desired_hash(verified_ids)

    # Both comparisons tolerate in-place id canonicalisation (docs/03 s.8):
    # the id the plan inserted may be stored as the song's canonical variant.
    if matches_up_to_substitutions(verified_ids, desired):
        publication.status = "COMPLETE"
        publication.remaining_item_changes = 0
        publication.remaining_estimated_requests = 0
        manifest.last_published_at = now
        manifest.next_publish_after = now + PUBLISH_WINDOW
    elif matches_up_to_substitutions(verified_ids, list(plan_slice.expected_order)):
        publication.status = "PARTIAL"
        publication.remaining_item_changes = plan_slice.remaining_item_changes
        publication.remaining_estimated_requests = plan_slice.remaining_requests
        publication.expected_intermediate_hash = verified_hash
        publication.not_before = now + PUBLISH_WINDOW
        manifest.last_published_at = now
        manifest.next_publish_after = now + PUBLISH_WINDOW
    else:
        publication.status = "FAILED"
        publication.error_code = "VERIFICATION_MISMATCH"

    publication.verification_hash = verified_hash
    db.flush()

    logger.info(
        "publish window finished",
        extra={
            "operation": "playlist_publish",
            "outcome": publication.status,
            "playlist_id": manifest.playlist_id,
            "remaining": publication.remaining_item_changes,
        },
    )
    return publication


def _plan_from_slice(manifest: ManagedPlaylist, plan_slice: PlanSlice) -> PlaylistDiffPlan:
    assert manifest.playlist_id is not None
    return build_plan(manifest.playlist_id, manifest.ownership_marker, plan_slice.operations)
