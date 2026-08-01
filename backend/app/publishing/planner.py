"""Minimal, convergent playlist diff (docs/03 section 8).

Removes first, then adds, then moves. The move planner walks left to right
and pins each item at its final position, so repeating the plan across
windows converges instead of chasing a moving target.

A move needs the ``setVideoId`` that only a fresh ``get_playlist`` provides,
so freshly added tracks cannot be reordered in the same window — they are
ordered in the next one, after the verification read. Every window is capped
both by logical item changes and by mutating HTTP requests.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.domain.catalog import PlaylistDiffPlan, PlaylistOperation

MAX_ITEM_CHANGES_PER_WINDOW = 15
MAX_MUTATING_REQUESTS_PER_WINDOW = 15


@dataclass(frozen=True, slots=True)
class RemoteItem:
    video_id: str
    set_video_id: str


@dataclass(frozen=True, slots=True)
class PlannedMove:
    """A move plus the local information needed to simulate it."""

    operation: PlaylistOperation
    before_video_id: str | None


@dataclass(frozen=True, slots=True)
class PlanSlice:
    operations: tuple[PlaylistOperation, ...]
    remaining_item_changes: int
    remaining_requests: int
    expected_order: tuple[str, ...]
    complete: bool

    @property
    def item_change_count(self) -> int:
        return len(self.operations)

    @property
    def request_count(self) -> int:
        return _estimate_requests(list(self.operations))


def _estimate_requests(operations: list[PlaylistOperation]) -> int:
    """Batched add/remove cost one request each; every move costs one."""
    moves = sum(1 for op in operations if op.kind == "MOVE")
    has_add = any(op.kind == "ADD" for op in operations)
    has_remove = any(op.kind == "REMOVE" for op in operations)
    return moves + int(has_add) + int(has_remove)


def full_operations(remote: list[RemoteItem], desired: list[str]) -> list[PlaylistOperation]:
    """Every operation this window could legally apply, in dependency order."""
    desired_set = set(desired)
    set_video_ids = {item.video_id: item.set_video_id for item in remote}

    operations: list[PlaylistOperation] = []
    order = [item.video_id for item in remote]

    for item in remote:
        if item.video_id not in desired_set:
            operations.append(
                PlaylistOperation(
                    kind="REMOVE", video_id=item.video_id, set_video_id=item.set_video_id
                )
            )
            order.remove(item.video_id)

    for video_id in desired:
        if video_id not in set_video_ids:
            operations.append(PlaylistOperation(kind="ADD", video_id=video_id))
            order.append(video_id)

    for index, video_id in enumerate(desired):
        if index >= len(order):
            break
        if order[index] == video_id:
            continue
        if video_id not in set_video_ids:
            # No setVideoId yet: this track was added in this very window and
            # can only be positioned after the next fresh read.
            continue
        before_video_id = order[index]
        before_set_video_id = set_video_ids.get(before_video_id)
        if before_set_video_id is None:
            continue
        operations.append(
            PlaylistOperation(
                kind="MOVE",
                video_id=video_id,
                set_video_id=set_video_ids[video_id],
                before_set_video_id=before_set_video_id,
            )
        )
        order.remove(video_id)
        order.insert(index, video_id)

    return operations


def apply_operations(
    remote: list[RemoteItem], operations: list[PlaylistOperation], desired: list[str]
) -> list[str]:
    """Predict the remote order after a slice, for the verification read."""
    order = [item.video_id for item in remote]
    desired_index = {video_id: index for index, video_id in enumerate(desired)}

    for operation in operations:
        if operation.kind == "REMOVE":
            if operation.video_id in order:
                order.remove(operation.video_id)
        elif operation.kind == "ADD":
            order.append(operation.video_id)
        elif operation.kind == "MOVE" and operation.video_id in order:
            target = desired_index.get(operation.video_id, len(order) - 1)
            order.remove(operation.video_id)
            order.insert(min(target, len(order)), operation.video_id)
    return order


def plan_window(
    remote: list[RemoteItem],
    desired: list[str],
    *,
    max_item_changes: int = MAX_ITEM_CHANGES_PER_WINDOW,
    max_requests: int = MAX_MUTATING_REQUESTS_PER_WINDOW,
) -> PlanSlice:
    """Take as many operations as both budgets allow, in a stable order."""
    operations = full_operations(remote, desired)

    chosen: list[PlaylistOperation] = []
    requests = 0
    used_add = False
    used_remove = False

    for operation in operations:
        if len(chosen) >= max_item_changes:
            break
        if operation.kind == "MOVE":
            cost = 1
        elif operation.kind == "ADD":
            cost = 0 if used_add else 1
        else:
            cost = 0 if used_remove else 1
        if requests + cost > max_requests:
            break
        chosen.append(operation)
        requests += cost
        used_add = used_add or operation.kind == "ADD"
        used_remove = used_remove or operation.kind == "REMOVE"

    expected_order = apply_operations(remote, chosen, desired)
    remaining_operations = operations[len(chosen) :]

    # Tracks added in this window still need positioning next time; count
    # them so the UI can show honest "remaining" numbers.
    pending_moves = _pending_move_estimate(expected_order, desired)
    remaining_items = len(remaining_operations) + pending_moves

    return PlanSlice(
        operations=tuple(chosen),
        remaining_item_changes=remaining_items,
        remaining_requests=_estimate_requests(remaining_operations) + pending_moves,
        expected_order=tuple(expected_order),
        complete=remaining_items == 0 and list(expected_order) == desired,
    )


def _pending_move_estimate(current: list[str], desired: list[str]) -> int:
    """How many items are still out of place once this window is applied."""
    if list(current) == desired:
        return 0
    return sum(
        1
        for index, video_id in enumerate(desired)
        if index >= len(current) or current[index] != video_id
    )


def build_plan(
    playlist_id: str,
    expected_marker: str,
    operations: tuple[PlaylistOperation, ...],
) -> PlaylistDiffPlan:
    return PlaylistDiffPlan(
        playlist_id=playlist_id, expected_marker=expected_marker, operations=operations
    )


def with_refreshed_set_video_ids(
    plan: PlaylistDiffPlan, remote: list[RemoteItem]
) -> PlaylistDiffPlan:
    """Re-bind setVideoIds from the latest read before writing."""
    mapping = {item.video_id: item.set_video_id for item in remote}
    operations = tuple(
        replace(
            operation,
            set_video_id=mapping.get(operation.video_id, operation.set_video_id),
        )
        for operation in plan.operations
    )
    return replace(plan, operations=operations)
