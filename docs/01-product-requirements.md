# Product requirements (BRD)

| Field | Value |
| --- | --- |
| Product | YouTube Music Tuner |
| Document version | 1.2.1 |
| Date | 2026-08-01 |
| Owner | the single local user |
| Type | personal-use, local-first, non-commercial |
| Status | approved for implementation |

## 1. Problem

The user's regular playlists become monotonous over time, and YouTube Music's automatic recommendations do not give sufficiently clear control over the balance between familiar and new music. The service also does not give the user a rich and transparent model of which specific actions influenced the selection.

A local layer is needed that:

- knows the liked tracks, existing playlists and available history;
- sees the actual behaviour inside its own player;
- distinguishes a deliberate early skip from a pause, buffering or closing the tab;
- builds an endless "Wave" queue with an adjustable temperature;
- maintains, without manual assembly, useful private playlists in YouTube Music for listening in the official app, including on iPhone;
- does not poll YouTube Music constantly and does not rewrite playlists without need.

## 2. Vision

Tuner is a calm personal player that starts from already-known taste, gradually learns from behaviour and changes the selection in an explainable way. The user chooses the mood and the degree of exploration of new material; everything else happens automatically and locally.

## 3. Target user

A single technically literate owner of a Google/YouTube Music account. A multi-user model, registration, billing, public access and commercial operation are not planned.

## 4. Goals

### 4.1. Product

- Reduce the share of early manual skips relative to the starting baseline.
- Preserve the recognisability of the musical taste while adding controlled novelty.
- Provide one action to start the Wave and two simple controls: temperature and context/mood.
- Make learning continuous and not requiring a manual start.
- Provide a useful result in the official YouTube Music as well, through managed playlists.

### 4.2. Technical

- Store raw telemetry and aggregates only locally.
- Separate the unstable `ytmusicapi` integration from the domain logic.
- Limit external calls with cooldown, TTL caches, job-lock and incremental diff.
- Ensure a repeatable launch in Docker Compose on macOS with Apple Silicon.

## 5. Non-goals of the first version

- Copying the interface, branding or source code of Yandex Music.
- Extracting, downloading, transcoding or storing YouTube audio streams.
- Playback from a tab/screen that the user is not currently viewing, or with a hidden or masked iframe.
- A full replacement of the official mobile client.
- Synchronising exact skips and listening duration from the official app: YouTube Music does not provide them to Tuner.
- Social features, shared playlists, podcasts, uploading one's own files.
- A mandatory external LLM, cloud analytics or sending history to third parties.
- Access from the internet or the local network without a separate secured configuration.

## 6. Main use cases

### UC-01. Initial connection

The user creates an OAuth client in Google Cloud and performs the device flow, after which Tuner confirms the account and starts the initial synchronisation of likes, playlists and available history.

### UC-02. Starting the Wave

The user opens the home page, chooses the temperature and mood, and presses Play. The first track starts playing after at most one explicit user action; the queue has already been prepared from the local cache.

### UC-03. Learning from listening

During playback Tuner locally accounts for the actual time played, listening to the end, an early move to the next track, a repeat, seeking backward and an explicit rating. After a sufficient number of new observations the model updates automatically.

### UC-04. Temperature control

At low temperature the queue relies on likes and close recommendations; at high temperature it widens the circle of artists and sources, but does not include blocked or known-weak candidates.

### UC-05. Publishing to YouTube Music

After a new signal has accumulated and the cooldown has expired, Tuner recalculates three stable private playlists: `Tuner · Familiar`, `Tuner · Balance`, `Tuner · Discovery`. It changes only its own playlists, makes a backup of their contents, applies a bounded diff and verifies the result.

### UC-06. Listening on iPhone

The user opens one of the Tuner playlists in the official YouTube Music app. This listening contributes to the overall YouTube Music profile in the usual way, but exact Tuner telemetry is unavailable for it; at the next infrequent synchronisation only the available history can be obtained.

## 7. Functional requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-001 | Show the OAuth state and a clear initial authorisation flow. | Must |
| FR-002 | Synchronise liked tracks, library playlists and the history available through `ytmusicapi`. | Must |
| FR-003 | Show the library and playlists from the local cache without an external request for every screen. | Must |
| FR-004 | Search for tracks and add the selected result to the local queue. | Should |
| FR-005 | Play tracks by `videoId` through a visible YouTube IFrame Player. | Must |
| FR-006 | Support play/pause, next, previous, seek, volume, queue and repeat. | Must |
| FR-007 | Collect versioned, idempotent telemetry with exact `played_seconds` and a periodic `progress_tick` that limits progress loss on a crash. | Must |
| FR-008 | Provide explicit like/dislike and synchronise them with YouTube Music with debounce. | Must |
| FR-009 | Generate the local Wave queue without contacting YouTube on every next. | Must |
| FR-010 | Support temperature 0–100 and an independent context/mood. | Must |
| FR-011 | Explain why a track was included with short factors: familiar artist, similar to a seed, not played for a long time, exploring new music. | Should |
| FR-012 | Train the model automatically only after the threshold of quality sessions is reached. | Must |
| FR-013 | Automatically update only playlists created and registered by Tuner. | Must |
| FR-014 | Show the time of the last synchronisation, the last training, the next allowed publish and the API budget consumption. | Must |
| FR-015 | Allow disabling auto-publishing, deleting local telemetry and revoking the connection. | Must |
| FR-016 | Create a snapshot of the remote playlist before writing and be able to restore it manually. | Must |

## 8. Business rules

- BR-001: all managed playlists are private by default.
- BR-002: other people's and manually created playlists are only read; Tuner does not change them.
- BR-003: an explicit dislike always excludes the track until it is manually reverted.
- BR-004: pause, buffering, player error and the tab going to the background do not count as a negative reaction.
- BR-005: an "early skip" occurs only after an explicit next / choosing another track. When the duration is known, the boundary is less than 20%; when it is unknown, after the duration is requested from the IFrame, a less confident absolute-time fallback of up to 30 seconds is applied.
- BR-006: a session qualifies for training if at least 10 actual seconds were played or there was an explicit like, dislike or Next. Therefore a deliberate early Next is not lost even in the first seconds of a track; pause, close and error still do not qualify a session by themselves.
- BR-007: the local queue may be rebuilt often without external calls; remote playlists — no more than one publish window per 24 hours.
- BR-008: one subsequent publish changes no more than 15 logical items and makes no more than 15 mutating HTTP calls in each playlist per window; the initial fill is performed by a single confirmed `create_playlist(..., video_ids=...)` and is verified immediately.
- BR-009: after the bootstrap threshold the model trains in shadow mode, but the first 100 qualified sessions are served by an unchanged rule-based ranker; the model's influence on the tracks served begins only after the clean baseline is closed and the safety gates are passed.
- BR-010: the default target size of a managed playlist is 60, but the publish planner may deterministically reduce it to the largest achievable size of no less than 25 if otherwise the temperature quota and the other quality gates cannot be satisfied. If even 25 tracks cannot be assembled, no external write is performed and the UI shows the exact shortfall per bucket.
- BR-011: continuing an already accepted publication with status PARTIAL does not require another 15 new sessions: it is the completion of an immutable plan. Each subsequent fragment still waits for a new 24-hour window and passes the safety, ownership, hash, circuit and call-budget checks again.
- BR-012: two adjacent waves overlap by no more than 30%, and the discovery part is not repeated at all. Freshness is a constraint, not a side effect of ranking: in a conflict with the familiar quota freshness wins, and a quota shortfall is explicitly marked with a reason code. A repeated like is acceptable — the complaint is always about the repeated serving of what was skipped.
- BR-013: a remote playlist is never published without first previewing its contents. The previewed list is frozen, and the write is performed with exactly that list; the publish button is unavailable before the preview.
- BR-014: any managed playlist can be deleted on explicit confirmation, including ACTIVE. The safety guarantee is the match of the ownership marker on a fresh read, not the record status.
- BR-015: every track from the likes library must be used as a graph exploration point before the traversal goes deeper. A pool that reflects part of the taste is perceived as monotony even when the tracks formally do not repeat.

## 9. Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-001 | One Docker Compose service, a persistent volume and a health check. |
| NFR-002 | Bind by default only to `127.0.0.1:43127`; the port is configurable. |
| NFR-003 | Cached API response p95 under 250 ms on a local machine. |
| NFR-004 | No OAuth token, client secret or cookie ends up in the logs. |
| NFR-005 | Events are accepted idempotently by `client_event_id`. |
| NFR-006 | A container restart does not lose the DB, OAuth or the unfinished publish state. |
| NFR-007 | All schema changes are made by Alembic migrations. |
| NFR-008 | External errors lead to backoff, not to a tight retry loop. |
| NFR-009 | The UI remains usable at a width of 1024 px; mobile web is best effort, not a primary goal. |
| NFR-010 | The algorithm must be reproducible with a fixed seed and a data snapshot. |

## 10. Success metrics

The baseline is collected on the first 100 qualified sessions, all of which are ranked by a single fixed version of the rule-based score. From the 40th session LinUCB may be trained and evaluated only in shadow mode, but does not influence track selection. After the baseline is closed and the next 200 sessions with the active model, the MVP is considered useful if:

- the share of explicit skips before 20% of the duration decreased by at least 20% relative to the baseline;
- the share of tracks listened to at least 90% of their length increased by at least 15% relative to the baseline;
- in Balance mode at least 30% of played tracks are not from likes, but no more than 40% of them receive an early skip;
- the overlap of adjacent waves does not exceed 30%, and there is no repetition of discovery tracks;
- a wave of 40 tracks contains at least 30 different artists, and no artist is present in all of the latest waves;
- every playable like is used as a graph exploration point;
- the user manually cleans the managed playlists no more than once a week;
- the automation performs no more than the set daily budget of external operations;
- there are no changes to playlists not registered as `managed_by_tuner`.

With a small amount of data the metrics are shown together with the number of observations, without false confidence.

The main early-skip/completion metrics include only sessions with `classification_basis=RATIO`. Sessions with an unknown duration and the absolute-time fallback are shown as a separate series and participate in training with lower weight, but do not distort the baseline of the "before 20%" and "at least 90%" shares.

## 11. MVP definition of done

The MVP is ready when all Must requirements are met, scenarios AT-01…AT-12 from the testing document pass, the container recovers after a restart without data loss, and one real end-to-end smoke test confirms reading likes, playback, writing telemetry and safe updating of a test private playlist.
