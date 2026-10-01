# YouTube Music integration

## 1. Decision

`ytmusicapi` is used to read the library, generate candidates and modify playlists, starting from the exactly pinned version `1.12.1`. Playback is performed not by this library but by the YouTube IFrame Player API in the browser.

The reasons and the viability check of the library are recorded in [ADR-001](decisions/ADR-001-use-ytmusicapi.md). The risk of an unofficial API is accepted only for a personal local tool.

## 2. Authentication

### Verified fact: a custom OAuth client does not work

Check of 2026-08-01 on a real account: the device flow with a custom Google Cloud client of type `TVs and Limited Input devices` succeeds, and Google returns a valid refresh token with scope `https://www.googleapis.com/auth/youtube`, but **any** subsequent YouTube Music internal-API call responds with `HTTP 400 Bad Request: Request contains an invalid argument` — including `get_account_info`, `get_liked_songs`, `get_library_playlists`, `get_history` and `search`. An unauthenticated `search` through the same container works, which means the transport and the parsers are sound.

Conclusion: YouTube Music accepts Bearer tokens only from Google's own clients, not from a client created by the user. The token is formally valid but useless for the internal API.

### Primary option — browser authentication

Three equivalent paths, all of which save the same file `/data/secrets/browser.json` with permissions `0600`:

1. **The Connect button in Settings.** The user copies the request headers from DevTools and pastes them into the field. `POST /api/v1/auth/browser-headers` goes through the same guards as any mutation: exact Origin, HttpOnly SameSite=Strict cookie, CSRF token, Host allowlist. The value is not returned in the response and is not logged.
2. **`./connect.sh`.** Opens a visible browser window, waits for the login, intercepts the headers of a real `/youtubei/` request, imports them and wipes the intermediate file.
3. **`python -m app.cli browser import -`.** A manual path for when the UI is unavailable.

Every path supplements the pasted input with what is usually lost when copying: `authorization` is derived from the cookie (`__Secure-3PAPISID`), `x-goog-authuser` defaults to `0`. Success is confirmed only after a real `get_liked_songs(limit=1)`; if YouTube Music rejects the headers, the file is deleted rather than left broken.

State check: `python -m app.cli status` or Settings — `method=BROWSER` is expected.

Cookies last a long time but not forever: on `AuthError` the UI shows reconnect, and the headers are copied again. This is the price of working on top of an unofficial interface.

### Fallback option — OAuth device flow

Kept in the code in case Google starts accepting user-created clients again: `credentials import` → `auth` → `/data/secrets/oauth.json`. The adapter uses OAuth only if `browser.json` is absent.

### Boundary change: the UI accepts cookies

The original rule "no credential passes through the web UI" was written on the assumption that OAuth would work. Once OAuth turned out to be unusable, the only way to connect was to enter the headers manually in the terminal, which is disproportionately inconvenient for a local personal tool. The decision was revisited: **cookies are accepted by a form in Settings**, because the risk does not change qualitatively — the interface is reachable only on loopback, CORS is disabled, the Host allowlist, exact Origin, SameSite=Strict cookie and CSRF token are in force, the CSP forbids third-party scripts, and the cookies themselves already reside in the user's browser.

What remains unchanged: the value is never returned in a response, never reaches the logs (the log filter redacts `cookie` and `authorization`), is stored only as a `0600` file inside the container and is deleted on `disconnect`.

**The client secret is still not accepted through the UI** — only via `python -m app.cli credentials import`.

## 3. Adapter boundary

The domain calls an interface that does not depend on the `ytmusicapi` format:

```python
class MusicCatalogPort(Protocol):
    def account(self) -> Account: ...
    def liked_tracks(self, limit: int | None = None) -> list[Track]: ...
    def library_playlists(self) -> list[RemotePlaylist]: ...
    def playlist(self, playlist_id: str) -> RemotePlaylistSnapshot: ...
    def history(self) -> list[RemoteHistoryItem]: ...
    def related(self, video_id: str) -> list[TrackCandidate]: ...
    def radio(self, video_id: str, limit: int) -> list[TrackCandidate]: ...
    def rate_track(self, video_id: str, rating: Rating) -> None: ...
    def create_private_playlist(self, title: str, description: str, video_ids: list[str]) -> str: ...
    def apply_playlist_diff(self, plan: PlaylistDiffPlan) -> None: ...
    def delete_managed_playlist(self, playlist_id: str, expected_marker: str) -> None: ...
```

The adapter must:

- convert external dict payloads into internal typed models;
- validate the required `videoId`, title and artist list;
- keep `None` instead of inventing a missing album/duration;
- classify errors as `AuthError`, `RateLimited`, `RemoteChanged`, `ParseError`, `Unavailable`;
- not let raw exceptions and external structures leak into the domain/UI.

## 4. ytmusicapi methods in use

| Task | Method | Frequency/cache |
| --- | --- | --- |
| Account check | `get_account_info()` | on startup and reconnect |
| Likes | `get_liked_songs()` | no more than once every 6 hours |
| Playlists | `get_library_playlists()` | no more than once every 6 hours |
| Playlist contents | `get_playlist()` | TTL 6 hours; a fresh read is mandatory before publish |
| Available history | `get_history()` | no more than once every 6 hours; auxiliary signal only |
| Search | `search()` | only on an explicit action, 400 ms debounce, TTL 24 hours |
| Related tracks | `get_song_related()` | a seed is not re-queried for 7 days; the retrieved edges remain in the graph |
| Radio/queue | `get_watch_playlist(..., radio=True)` | a seed is not re-queried for 7 days; the retrieved edges remain in the graph |
| Mood sources | `get_mood_categories()`, `get_mood_playlists()` | TTL 30 days/7 days |
| Like/dislike | `rate_song()` | state changes only, debounce/idempotency |
| Creation | `create_playlist(..., video_ids=desired)` | only a confirmed initial managed setup |
| Modification | `add_playlist_items()`, `remove_playlist_items()`, `edit_playlist()` | publish job |
| Deletion | `delete_playlist(playlistId)` | only an explicitly confirmed deletion of one's own managed playlist after a fresh marker check |

Uploading music, and deleting other people's playlists and ordinary user entities, are out of scope. The only exception is deleting one's own managed playlist (UNVERIFIED, CLEANUP_REQUIRED or ACTIVE) with the exact ID, a fresh ownership marker and a separate confirmation: these are the listener's playlists, and they are protected by the marker, not by the status.

## 5. Library synchronisation

Synchronisation is snapshot-oriented:

1. Check the cooldown and OAuth.
2. Create a `sync_run` with status RUNNING.
3. Fetch the likes and the playlist list.
4. Normalise tracks by `videoId`; store entries without a `videoId` as metadata-only and do not place them in the queue.
5. Upsert remote entities without deleting local telemetry.
6. For disappeared objects, set `remote_deleted_at` rather than physically deleting them.
7. Save the watermark and counts.
8. Finish with SUCCESS or FAILED with a safe short error code.

The YouTube Music history is not used to infer "the user skipped a track": it has no reliable listening duration. It only raises recency/fatigue and confirms that a track was recently played outside Tuner.

## 6. Like/dislike updates

The UI first records the explicit event locally. There is one mutable command per track, with dedupe key `rating:{video_id}`; `desired_state` and a monotonically increasing `revision` live in the payload. Each quick toggle transactionally updates the payload of the existing PENDING command and moves `not_before` to two seconds after the last action.

Before the call, the worker reads the newest revision. After the external response it marks the rating as synchronised only if the revision still matches. If the user managed to change the state while the job was RUNNING, the completed operation does not confirm the new state: after the dedupe key is released, a successor job with the latest payload is created/resumed. This way a like → dislike sequence never leaves a stale like as the final state.

A failure of the external write does not roll back the local behaviour event, but the UI shows `not synced`. The retry reads the current desired state again, not the payload of the initial attempt.

## 7. Candidate generation without API spam

Candidate refresh runs separately from ranking:

- select at most 6 seeds per run from positive roots — likes **and** tracks with a strong local reward; favourites not yet expanded go first, because an unexpanded like gives the pool nothing;
- for each seed make at most one `related` and one `radio` call, and only if the seed has not been queried for a while;
- store the edge `seed → candidate`, the source, the position, the distance from the root (`hop`) and the retrieval time;
- deduplicate by `videoId`, but **do not collapse multiplicity**: the number of distinct favourite tracks pointing to a candidate is the strongest available signal;
- do not refresh the candidate pool automatically more than once a day; expansion of the graph frontier runs as a separate job every 6 hours;
- when Wave is opened, work only with the local pool;
- when the pool is empty, allow one foreground refresh operation with an explicit UI status.

## 8. Managed playlists

By default Tuner maintains exactly three stable playlists:

- `Tuner · Familiar` — temperature 20;
- `Tuner · Balance` — temperature 50;
- `Tuner · Discovery` — temperature 80.

On first creation a marker of the form `Managed by YouTube Music Tuner; instance=<uuid>; schema=1` is written to the description. The local table stores a nullable playlist ID and the same instance UUID. An ordinary publish requires status ACTIVE and a matching ID/marker; for UNVERIFIED only verify/adopt or an explicitly confirmed cleanup are allowed.

### Initial creation

After an explicit preview/confirmation, each desired list first passes `playlist-gates-v2`, including the selection of `effective_target_size` from 25 to the configured default of 60. Before the external call, Tuner transactionally creates a local setup intent with status CREATING, the instance UUID, the ownership marker and the accepted desired hash. Then a single `create_playlist(title, description, privacy_status="PRIVATE", video_ids=desired)` is called with all `effective_target_size` tracks in the target order. This is an initial-create operation, not an incremental diff, so the limit of 15 item changes does not apply to it.

Immediately after the playlist ID is returned, it is written to the same manifest, the status changes to UNVERIFIED **before** the verification read, and the write is **committed**: a flush alone is not enough, because a process crash would roll it back, and an open transaction also holds SQLite's single write lock for the whole duration of the external calls.

Then Tuner calls `get_playlist(limit=None)` and checks the marker, effective size, uniqueness and the full order. The read is performed up to three times (immediately, after 2 seconds and after 5 seconds): a just-created playlist cannot be read instantly — YouTube responds with an incomplete payload that does not parse. One attempt was not enough, and all three playlists remained UNVERIFIED even though they had been created correctly.

YouTube does not guarantee the order of a batch insert: a list of 60 tracks may land in a different order. Therefore, if the marker, uniqueness and the **full composition** match, the playlist is accepted as ACTIVE even with a different order: the first publish will deterministically restore the order with its move operations.

In addition, YouTube **canonicalises ids on insert**: confirmed on a live Discovery playlist (2026-08-09), where `aQsFHgp4ra8` landed as `0BW8eeCK5g4` — a different id of the same song, at exactly the same position, with the rest of the order unchanged; re-creating the playlist reproduces the substitution deterministically. Such a playlist is unquestionably ours: we had just written this list, the marker matched, and all other positions are identical. Verify accepts positional substitutions of up to 10% of the list (no lower threshold: for fewer than ten tracks, none) as ACTIVE and **adopts the actual remote list** — the accepted hash is updated to the remote one so that subsequent verify/publish do not fight the canonicalisation. A composition mismatch beyond this remains UNVERIFIED. Teaching a persistent alias (the desired list uses the canonical id right away) is a separate task.

The order is checked against the **hash of the agreed-upon list** recorded in the manifest, not against a freshly generated list: the queue is different every time, so a repeated `Verify/adopt` would otherwise always return `VERIFICATION_MISMATCH`.

On success the manifest becomes ACTIVE. On a mismatch or a read error it stays UNVERIFIED or gets CLEANUP_REQUIRED; Tuner does not automatically try to "top up", reorder or create a replacement.

After a crash/restart, setup first resumes CREATING/UNVERIFIED manifests rather than creating a new playlist. If the create may have gone through but the response was not saved, reconciliation searches the fresh remote playlists only for the exact instance marker and expected title, then checks the desired hash. A single exact match is accepted as UNVERIFIED and goes through verify; zero matches leave it in CREATING for a safe manual retry; several matches move the setup to CLEANUP_REQUIRED. The UI allows `Verify/adopt` separately, or deleting only the exact ID with a matching marker after explicit confirmation. An arbitrary playlist, or a manifest without a matching marker, cannot be deleted by this path.

Cleanup calls `delete_managed_playlist(playlist_id, expected_marker)`. The port implementation first performs a fresh `get_playlist(limit=None)` and compares the exact marker, then performs exactly one `delete_playlist(playlistId)`. A confirmed success moves the manifest to DELETED. If the delete response is lost/ambiguous, the manifest stays CLEANUP_REQUIRED; there is no blind retry. The next explicitly confirmed reconciliation starts with a new read: remote not-found moves the manifest to DELETED, a found exact marker permits a new single attempt, a mismatch forbids deletion. A CREATING intent is deleted by the same path: without a remote ID it is a purely local record, with an ID the same marker check applies.

**DELETED is terminal for reconcile.** Reconcile adopts and verifies but never creates; for a DELETED manifest it responds with a validation error rather than "resurrecting" the record into CREATING — otherwise a deleted playlist would look as if it were being created forever. Re-creation goes only through setup.

**Setup is idempotent.** An ACTIVE kind is skipped without an external call (`alreadyExisting`) — an ACTIVE kind reaching the create branch would mean a duplicate on YouTube. A CREATING row without an ID but with a recorded create error first goes through adopt-by-marker and creates anew only on zero matches: a create that looked failed from its response may have succeeded on YouTube's side. Reusing a row (a DELETED tombstone or an abandoned intent) resets the publish pacing (`last_published_at`, `next_publish_after`) and the stored preview — from the publication's point of view this is a new playlist, and the first publish must not run into the daily window of its previous life.

**A PARTIAL publication does not survive playlist re-creation.** The continuation stores the snapshot and expected hash of the deleted list, taken from a playlist that no longer exists; continuing it against a new playlist guarantees `REMOTE_CHANGED` (observed live on 2026-08-09). A PARTIAL created before the `setup_started_at` of the manifest's current life is considered stale: publish marks it `FAILED/SUPERSEDED_BY_SETUP` and starts a new cycle with a fresh snapshot.

### Subsequent publications

The publish algorithm:

1. Acquire the lease and check the daily cooldown.
2. If a PARTIAL publication exists, continue its fixed desired snapshot; a newer generation waits and does not change the moving target. This continuation does not require 15 new sessions or a new rating, but runs only in the next 24-hour window. A new explicit dislike/block or the unavailability of a target item invalidates the plan before the next write.
3. Fetch a fresh remote snapshot and make sure that the playlist is registered, the marker matches, and the remote hash corresponds to the last verified intermediate state.
4. Save a full backup of the current order.
5. For a new cycle only, check the trigger of 15 new qualified sessions/a changed rating, build the desired list with an adaptive `effective_target_size`, and check all playlist quality gates. For PARTIAL, use the stored desired list and re-check the hard/safety gates.
6. Compute a deterministic diff; save the plan and an immutable desired snapshot with status PLANNED.
7. Select a fragment that simultaneously fits within no more than 15 logical item changes and 15 mutating HTTP requests per playlist per window: remove the unwanted items, add the missing ones, then move for ordering. A batch add/remove counts as one HTTP request, but every affected item consumes the item-change budget; each move requires a separate request. The planner stops before exceeding either of the two limits.
8. For move, use `edit_playlist(moveItem=(setVideoId, beforeSetVideoId))`: `setVideoId` is taken only from a fresh `get_playlist()`. A stable left-to-right planner pins each moved element to its final position.
9. Apply the selected remove/add/move; status WRITING. Re-read the playlist; status VERIFYING.
10. If the remote matched the expected intermediate snapshot but not yet the desired one, save PARTIAL and continue the same plan in the next publish window. On a full match — COMPLETE. In both cases the comparison tolerates positional id canonicalisation (the same substitution "a different id of the same song at the same position" as in setup-verify, up to 10% of the list): YouTube may store a track inserted by the plan under a canonical id, and without the tolerance every publication of such a list would fail with VERIFICATION_MISMATCH (observed live on 2026-08-09 on Familiar and Discovery). `verification_hash`/`expected_intermediate_hash` record the actual remote, so the continuations are self-consistent. Any discrepancy beyond the tolerance — FAILED without a blind retry.

Fixing the desired snapshot guarantees convergence: even if a new recommendation has already been computed, an unfinished plan does not chase a daily-changing target. In the worst case a complex reordering converges over several 24-hour windows; the scheduler continues PARTIAL without a new listening signal, and the UI displays the remaining item changes, requests and target generation.

Rollback is a separate manual operation from the backup. An automatic rollback could aggravate a partially successful write and therefore is not started without a check.

## 9. Internal call budget

This is not an official quota but an application safeguard:

| Category | Automatic limit |
| --- | --- |
| Full library sync | 4 times per day |
| Candidate refresh | 1 per day, at most 6 seeds |
| Graph frontier expansion | every 6 hours, at most 12 nodes per run (1 radio call each) |
| All discovery calls (`get_watch_playlist`, `get_song_related`) | at most 120 per day in total; graph expansion never displaces publishing |

The discovery-call ceiling exists so that a looping job does not hammer YouTube, not to save on exploration: covering every like with at least one request costs one call per track, and with sixty roots a smaller limit would stretch the representation of the whole taste over two days.
| Remote publish | 1 publish window per day |
| Initial creation of a managed playlist | one confirmed create with at most `configured_target_size` initial IDs and up to three verify reads; at most 4 playlist-endpoint requests per playlist |
| Subsequent item changes of one managed playlist | at most 15 logical item changes per window |
| Subsequent mutating requests of one managed playlist | at most 15 per window; batch add/remove = 1 request, move = 1 request |
| All playlist-endpoint requests of one managed playlist | at most 17 per window: 1 fresh read + up to 15 mutations + 1 verify read |
| Global automatic playlist budget | at most 51 playlist-endpoint requests per day for the three managed playlists; jobs run sequentially |
| Manual cleanup of a setup artifact | at most 2 playlist-endpoint requests per explicit confirmation: 1 fresh marker read + 1 `delete_playlist`; not counted in the automatic 51, but accounted for by the ledger/circuit and not retried blindly |
| Retry after a transient error | 60 seconds → 5 minutes → 30 minutes → circuit open |
| Search | only user input, debounce + cache |

Initial setup is a separate explicit operation: at most 12 endpoint requests for the three playlists (a create plus up to three verification reads for each), all recorded in the ledger; automatic publish is not started on the same day. Every external call is recorded in `api_call_ledger` with the playlist/publication ID and a read/mutation flag. When the item, per-playlist request or global request budget is exceeded, automatic jobs are deferred; a manual force action requires a separate confirmation in the UI and does not bypass request caps or the circuit breaker on an auth/rate-limit error.

## 10. Dependency update strategy

- The lockfile uses an exact version, with no floating range.
- Do not update automatically via a Dependabot merge without verification.
- Before an upgrade, run the adapter contract tests on recorded anonymised fixtures.
- Then run a read-only smoke test on a real account.
- A write smoke test is run only on a separate test private playlist.
- On a parser regression, roll back to the previous image; the DB migration must not depend on the new shape of the external payload.
- Check PyPI, the changelog, the freshness of `main`, open regression issues and the stable setup docs.
