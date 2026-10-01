# Player and telemetry

## 1. Playback principle

The frontend embeds the official YouTube IFrame Player and controls it through the JavaScript API. Tuner passes only the `videoId`; it does not extract the stream URL, does not proxy media and does not store audio.

The player remains visible, is not overlapped by the interface and has a viewport of at least 200×200 px. The recommended desktop size is 480×270. The iframe is given an exact `origin=http://127.0.0.1:43127`, taking the configurable port into account.

Tuner's own bottom bar provides quick play/pause/next/previous/like/dislike/volume controls but does not mask the mandatory iframe elements. Autoplay starts only after a user action, subject to the browser autoplay policy.

When the document transitions to `hidden`, Tuner records a neutral `visibility_changed`. Whether to pause at that point is decided by the `pause_on_hidden` setting, **off by default**. Context of the restriction: the [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies) prohibit a background player and define it as a player that is not displayed on a page, tab or screen being viewed by the user. An inactive tab falls under this definition.

Practice showed that this is not workable as the default behaviour: browsers report `hidden` whenever the window is covered by another application (occlusion on macOS), so the music cut off on every switch between applications. For a music player this is unacceptable.

Decision of the installation owner (2026-08-01): **nothing must interrupt playback**, so `Settings → Playback → Pause when this tab is hidden` is **off** by default.

- Off: the player keeps playing while the tab is hidden. The owner of a local non-commercial installation knowingly accepts the associated policy risk.
- On: a strict reading of the policy, pause on any `hidden`.
- The state is stored in `settings` and applied to the player when the UI loads.

The remaining policy requirements are met unchanged: the player is visible, is not covered by the interface, is not smaller than 200×200, mandatory elements are not masked, media is not downloaded.

## 2. PlayerPort

Outside the adapter, the UI works with this contract:

```typescript
interface PlayerPort {
  cue(videoId: string): Promise<void>;
  play(): void;
  pause(): void;
  seekTo(seconds: number): void;
  setVolume(percent: number): void;
  currentTime(): number;
  duration(): number;
  state(): PlayerState;
  subscribe(listener: (event: PlayerEvent) => void): () => void;
}
```

The adapter translates YouTube state codes into `UNSTARTED | ENDED | PLAYING | PAUSED | BUFFERING | CUED | ERROR`.

## 3. Event vocabulary

| Event | When it is created | Affects reward |
| --- | --- | --- |
| `track_cued` | the iframe has prepared a new `videoId` | no |
| `play_started` | first transition to PLAYING | session start |
| `play_resumed` | PLAYING after pause/buffer | not separately |
| `paused` | explicit/iframe pause | neutral |
| `buffering_started` | state BUFFERING | neutral |
| `progress_tick` | every 15 seconds of an active session; contains cumulative counters | heartbeat, saves progress, neutral by itself |
| `seek_forward` | position jumps forward | lowers the reliability of completion |
| `seek_backward` | position jumps backward | weak positive signal |
| `next_clicked` | the user explicitly pressed next/chose another track | classifies the skip |
| `previous_clicked` | the user went back | weak positive for the target track |
| `ended` | natural end | positive completion |
| `replay_started` | the same track was started again within 10 minutes | strong positive |
| `like_set` | explicit rating | strong positive |
| `dislike_set` | explicit rating | strong negative, block |
| `player_error` | the iframe returned an error (for example, 150 — embedding disallowed by the rights holder) | neutral for taste; closes the session with `termination_reason=player_error` and triggers a 24-hour candidate cooldown |
| `visibility_changed` | visible/hidden | diagnostic |
| `page_closing` | `pagehide`/closing | neutral |

## 4. How real time is calculated

`played_seconds` must not be computed as the maximum `currentTime`: a forward seek would falsely create a listen-through.

Once a second the frontend samples the monotonic clock and the player position. The interval is added to `played_seconds` only if:

- both the previous and the current state are PLAYING;
- the tab was not frozen;
- the monotonic delta is in the range 0–2.5 seconds;
- the change in player position is consistent with natural playback rather than a seek;
- there is no active buffering.

Additionally stored are `max_position_seconds`, `seek_forward_seconds`, `seek_backward_seconds`, `seek_forward_count`, `seek_backward_count`, `buffered_seconds` and `wall_clock_seconds`.

The effective duration is determined in this order:

1. a finite positive value of `YT.Player.getDuration()` after READY/PLAYING — `duration_source=PLAYER` and the most authoritative source;
2. a positive metadata duration from the catalogue — `duration_source=METADATA`;
3. otherwise `effective_duration_seconds=NULL`, `duration_source=UNKNOWN`.

If the IFrame later supplies the duration, the current session and the track cache are updated; a player/metadata discrepancy of more than two seconds is logged as a safe diagnostics metric, and PLAYER is used for classification. When the duration is known, position is capped at `duration + 5` seconds. When the duration is unknown, the backend does not apply this cap: it limits `played_seconds` to the sum of verified monotonic PLAYING intervals and to `wall_clock_seconds + 5`, so a large `positionSeconds` cannot create a listen.

## 5. Playback session

A session is identified by a UUID and belongs to a single `videoId`. It starts at the first PLAYING and is closed on:

- a natural ENDED;
- an explicit switch to another track;
- an error with no recovery within 30 seconds;
- the absence of a `progress_tick`/state-transition heartbeat for more than 2 minutes;
- a repeated start of the same track after the previous session was closed.

Closing the tab does not by itself mean a skip. An unfinished session gets `termination_reason=abandoned_unknown` and yields no negative reward.

A qualified session: at least 10 actually played seconds, or an explicit like, dislike or Next. A deliberate Next before the tenth second simultaneously closes the session, qualifies it and yields the corresponding early-skip reward; an automatic transition, a player error, a pause, hidden and closing the tab do not do this.

## 6. Event delivery

- Each event has a `client_event_id` UUID, `session_id`, `sequence_no`, `occurred_at`, `monotonic_ms`, `video_id`, `payload`, `schema_version=1`.
- The frontend accumulates a batch in memory and IndexedDB until the backend acknowledges it.
- Every 15 seconds of an active session a `progress_tick` is created with cumulative `played_seconds`, `position_seconds`, effective duration, seek/buffer counters and the current state. The cumulative form makes it safe to survive a repeated or missed batch without double summing.
- Right after a tick is created, the batch is sent; additional flushes are performed on track change, like/dislike, pause and visibility change.
- On `pagehide`, `navigator.sendBeacon` is used if available.
- The backend accepts repeated events safely; there is a unique constraint on `client_event_id`.
- A gap in `sequence_no` is flagged but does not block subsequent events.
- The server timestamp is stored separately from the client timestamp.
- On a crash without `pagehide`, no more than the current interval after the last created tick is lost — at most about 15 seconds; ticks that were already created but not acknowledged remain in IndexedDB and are sent after a reload.

## 7. Deriving behavioural signals

The backend aggregates events into a session summary. When the duration is known:

- `played_ratio = min(played_seconds / effective_duration_seconds, 1)`;
- `early_skip = explicit_next AND played_ratio < 0.20`;
- `mid_skip = explicit_next AND 0.20 <= played_ratio < 0.60`;
- `large_forward_seek = seek_forward_seconds >= max(30, 0.20 * effective_duration_seconds)`;
- `completed = played_ratio >= 0.90 OR (ended AND played_ratio >= 0.70 AND NOT large_forward_seek)`.

When the duration is unknown, `played_ratio=NULL` and `classification_basis=ABSOLUTE_TIME`:

- explicit next at `played_seconds < 30` → `early_skip=true`, a less confident raw reward `-2`;
- explicit next at `30 <= played_seconds < 120` → `mid_skip=true`, raw reward `-0.5`;
- explicit next after 120 seconds → neutral with respect to completion/skip;
- `large_forward_seek = seek_forward_seconds >= 30`;
- ENDED yields completion only when `played_seconds >= 60` and there is no large forward seek; otherwise `ended_unqualified=true` and the reward is neutral.

Also stored are `replayed`, `seek_backward_count`, `explicit_rating`, `qualified`, `reward`, `reward_version` and `classification_basis=RATIO|ABSOLUTE_TIME`. If the duration becomes known later and the raw events are still within retention, the session is recalculated by RATIO.

The reward rules are versioned. Raw events make it possible to deterministically recalculate only sessions within the 180-day retention; older summaries keep their original `reward`/`reward_version`. The new formula applies to the retained window and to new events, without rewriting the historical baseline retroactively.

Immediately after re-aggregation, the session is rolled up into the aggregates of the track and its artists (docs/07 section 4). This happens when the telemetry is received, not in a daily batch: a skip must affect the next track. While these aggregates were not being populated, the features for fatigue, recent skip, novelty and rediscovery were identically zero — that is, the listening history did not affect the results at all, even though the reward was computed correctly.

## 8. Queue

- The frontend keeps a queue snapshot with `queue_id`, the order and `generation_id`.
- At 3 tracks before the end, it requests a local continuation, not an external YouTube endpoint.
- Next atomically closes the current session before the next one starts.
- Previous returns the last track that was actually started, not simply the previous row of the snapshot.
- If the iframe cannot play a candidate, a neutral error is created and the next one is chosen. The error code matters: `101`/`150` mean embedding is prohibited by the rights holder — that is a property of the track, so it gets `is_playable=false` and no longer enters the queue; the other codes give the usual 24-hour cooldown. The first run on the live library showed 29 such tracks out of 51 likes, and without this distinction they would be skipped in every queue.
- An automatic transition after `ended` or `player_error` **does not send** `next_clicked`: the explicit-skip event is created only by a user action. Otherwise an unplayable track would receive a negative reward, as if it had been rejected.
- The queue history is not reshuffled when the temperature changes; the new value applies to the not-yet-played tail.
- A dislike itself switches to the next track: staying on a track that has just been rejected is pointless.
- A negative signal — a dislike, a veto or an explicit next before 60% of the track (120 seconds when the duration is unknown; the thresholds match the skip classification in aggregation) — rebuilds the not-yet-played tail with a local retune, debounced by ≈1.5 s. The selection adapts on every track, as in the reference Yandex Music "My Wave", without a single external call; the already-played head of the queue is untouchable.

## 9. What is visible when listening outside Tuner

In the official YouTube Music, Tuner does not see the exact position, pauses, skips and the reason for stopping. A periodic `get_history()` can provide the fact of a recent play, but such a signal is marked `source=remote_history`, gets a small recency weight and is not used as negative feedback.

Likes set in the official app will arrive on the next library sync and will be a strong positive signal. This is exactly why managed playlists are useful on an iPhone, but maximum learning accuracy is achieved when listening through Tuner's own web player.
