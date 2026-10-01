# Testing and acceptance

## 1. Strategy

Test pyramid:

- unit: domain rules, reward, ranking, diff, cooldown, telemetry aggregation;
- contract: ytmusicapi adapter on fixtures;
- integration: FastAPI + temporary SQLite + fake music provider;
- frontend component: player state and UI states;
- E2E: browser with a fake iframe/player port;
- real smoke: a minimal set on the real account and a separate test private playlist.

Real write tests are never run by the ordinary CI command.

## 2. Unit tests

### Telemetry

- forward seek does not increase `played_seconds`;
- buffering/pause does not count as playing;
- a duplicated event is ignored;
- an out-of-order batch is sorted by sequence when the gap is permissible;
- `progress_tick` every 15 seconds restores cumulative progress without double summation; a crash loses at most one interval;
- page close does not create an early skip;
- explicit next before 20% creates a negative signal;
- explicit next earlier than 10 seconds still qualifies the session; an automatic transition/error earlier than 10 seconds does not;
- duration is taken from PLAYER before METADATA; when duration is absent entirely, the exact 30/120-second fallback rules apply;
- `large_forward_seek` triggers at `max(30 seconds, 20% duration)`, and an ended event after such a seek does not get a false completion;
- like/dislike dominates implicit reward;
- completion `+2` is not summed with partial-listen `+1`, and the reachable implicit clamp is `[-3, 6]`;
- `reward=tanh(raw/4)` distinguishes completion, replay and like, and is checked with a tolerance of `1e-3`;
- changing the reward version recomputes only retained raw events and does not rewrite the frozen baseline/old summaries.

### Recommendation

- explicit dislike is absent at all temperatures;
- a fixed random seed reproduces the queue given the same database state; a repeated call in a row intentionally yields a different wave;
- two adjacent waves overlap by no more than 30%, the discovery part does not repeat;
- sampling by itself changes the order: two different rngs give different queues, the absence of an rng gives a deterministic one;
- temperature increases the discovery quota monotonically;
- one artist does not violate window limits;
- an empty mood pool is widened safely;
- a shortage of the familiar pool returns the actual mix and `FAMILIAR_POOL_WIDENED`, without violating the recent-track filter;
- the quota is two-sided: when both sides of the pool are sufficient, the actual mix equals the target within rounding accuracy, and an excess of familiar is possible only after admissible discovery is exhausted and is accompanied by `DISCOVERY_POOL_WIDENED`;
- `Rediscover` restricts the choice to tracks not played for at least 60 days, and when there are too few it degrades to a soft boost with `CONTEXT_WIDENED`; contexts without mood data do not change the output and always return `CONTEXT_WIDENED`;
- a "warm" track stops being counted as familiar after 60 days without listening and is classified as discovery;
- a track start marks its `played_at` on the wave that served it; retune replaces only the unplayed tail of the queue; 3 tracks before the end the queue continues with a local extend without an external call;
- negative artist affinity penalises candidates in proportion to confidence (`min(1, plays_all/3)`); likes are not penalised; the penalty for a veto artist and the penalty for negative affinity are not summed (max);
- veto excludes from the pool the track itself and all non-likes of its artist (waves and publications alike), sinks graph neighbours (−0.35), zeroes the seed weight and closes the node for expansion; a liked track of the artist survives the veto; lifting the veto restores everything except already recorded sessions (history is not rewritten);
- slop heuristic: a score ≥3 excludes the candidate from the output regardless of a like, ==2 penalises by −0.6; un-like in YouTube is not performed automatically;
- the farm detector auto-vetoes an artist (≥3 flagged, or ≥5 suspect, or templated titles + ≥1 suspect); an artist with a like is untouchable; templated titles without slop markers have no effect; a lifted auto-veto becomes `OVERRIDDEN` and will not be set again by the detector;
- a dislike automatically switches to the next track; a negative signal (dislike, veto, explicit next earlier than 60% / 120 s) rebuilds the unplayed tail of the queue with a debounced local retune, without touching the played head; a late explicit next does not rebuild the tail;
- reconcile for a DELETED manifest answers 400 and does not resurrect the record; `GET /playlists` does not contain DELETED manifests; setup skips ACTIVE kinds without an external call (`alreadyExisting`), does adopt-first for CREATING with a recorded create error, and when the row is reused resets publish pacing and the saved preview; a CREATING intent can be deleted via setup-artifact;
- verify accepts as ACTIVE a created playlist with the same composition but a different order (YouTube does not guarantee the order of a batch insert); positional id substitutions (YouTube canonicalisation) of up to 10% of the list are accepted with adoption of the remote list; a composition mismatch beyond the tolerance remains UNVERIFIED;
- a PARTIAL publication created before `setup_started_at` of the manifest's current life is not continued: it is marked `FAILED/SUPERSEDED_BY_SETUP`, and publish starts a new cycle;
- edges with an expired `expires_at` remain in the pool; support counts all seeds, not the best edge;
- graph expansion goes one hop further, skips already expanded nodes and stops when the discovery budget is exhausted;
- affinity aggregates are idempotent to repeated session aggregation and are updated on telemetry ingest;
- a like is not discounted for appearing in someone else's radio: its score equals the score of a like outside the graph;
- an unexpanded like outranks, at the expansion frontier, any already reachable candidate, however well supported that candidate is;
- all favourites are expanded before the traversal goes deeper;
- the desired list passes the gates by construction — quota, per-artist limit, no adjacent identical artists, minimum number of distinct artists and quality threshold;
- publishing mode does not apply novelty windows and wave history, so it contains more familiar tracks than a streaming wave;
- a failed periodic job is not queued again until the backoff expires; successful training without a new snapshot also waits; an open circuit does not postpone local jobs;
- a NaN snapshot is not activated;
- bootstrap does not start SHADOW before all four thresholds; sessions 1–100 are always served by the unchanged rule ranker, ACTIVE is impossible before 100;
- the baseline/SHADOW playlist gate uses `quality_expected=2*rule_score-1`, ACTIVE — LinUCB exploitation; `rule_score=0.40` passes the same `-0.20` boundary, and the exploration bonus does not bypass it;
- the ranking response unambiguously distinguishes the serving rule policy, the nullable shadow model and the ACTIVE serving model;
- the training watermark does not use one session twice.

### Publishing

- a missing playlist size is stored as NULL, not 0; Liked Music returns the local like count;
- preview returns tracks with title, artists and familiarity, not only counters;
- a repeated preview returns the same list, `regenerate` — a different one, and it becomes the new proposal;
- publish writes the previewed list, not a freshly regenerated one;
- an ACTIVE playlist is deleted upon explicit confirmation, any other status is rejected;
- verification repeats the read until a just-created playlist becomes readable, and preserves the ID when attempts are exhausted;
- verification is checked against the saved desired hash, so a repeated adopt passes while a playlist changed externally is still rejected;
- a non-managed playlist is always rejected;
- marker mismatch is always rejected;
- a remote hash conflict writes nothing;
- initial create saves a CREATING intent before the external call, saves the ID as UNVERIFIED immediately after the response, and makes it ACTIVE only after a full verify;
- a lost response/crash/verify failure does not create a duplicate playlist: restart reconciles the exact instance marker, and ambiguous matches require cleanup;
- cleanup may delete only an UNVERIFIED/CLEANUP_REQUIRED exact ID with a matching marker and explicit confirmation;
- a successful cleanup makes one fresh read and one delete and moves the manifest to DELETED; a marker mismatch does not call delete, and an ambiguous response leaves CLEANUP_REQUIRED without a blind retry;
- initial create passes `effective_target_size` IDs in one confirmed call and verifies the full order;
- every playlist quality gate has deterministic pass/fail fixtures, and a failure does not create an external call;
- the planner picks the maximum feasible `effective_target_size`: with the rest of the pool sufficient, 30 familiar in the Familiar policy give 42 and `TARGET_SIZE_REDUCED_FOR_POOL`; a size below 25 returns `INSUFFICIENT_POOL` with exact required/available counts and without an external call;
- unique primary artists are at least `ceil(0.40 * effective_target_size)` with max 3/artist and no adjacent same artist;
- a subsequent diff is minimal and at the same time bounded by 15 logical item changes and 15 mutating HTTP requests;
- batch add/remove consumes one request and an item count equal to the number of IDs; each move consumes one request; together with the fresh read/verify, 17 endpoint requests per playlist and 51 per day globally are not exceeded;
- a PARTIAL publication continues the previous desired hash without 15 new sessions, waits for the next 24-hour window, and the left-to-right move plan converges to the exact order in a finite number of windows;
- before every continuation, PARTIAL re-checks dislike/unavailable, ownership marker, remote hash, circuit and budgets; safety invalidation cancels the plan;
- repeating an idempotency key does not create a second publication;
- a restart in WRITING leads to a fresh read/verify, not to a repeat of add.

## 3. Contract tests ytmusicapi

Adapter fixtures must cover:

- liked track with/without album/duration;
- several artists;
- playlist continuation;
- unavailable/deleted item;
- localized metadata;
- related/radio empty response;
- auth/rate-limit/parse errors;
- create-with-videoIds/add/remove/edit-move/delete success shapes, including the mandatory `setVideoId` and the `delete_playlist` status/full-error response.

Fixtures are cleaned of OAuth, cookies, account identity and private playlist IDs. Contract tests check our typed results, not a full copy of the internal response.

When `ytmusicapi` is updated, the tests run first on the old fixtures, then a read-only recorder updates only the intentionally changed fixtures after manual review.

## 4. API integration

- migrations apply to an empty schema and to the previous schema;
- a telemetry batch partially accepts valid events;
- pagination stable;
- sync upsert does not erase local sessions;
- job lease works with two test workers;
- the circuit breaker blocks auto jobs;
- rapid rating switches use a single `rating:{video_id}` key; the final remote state corresponds to the maximum revision;
- the cleanup endpoint requires exact ID/marker/confirmation, creates at most two ledger entries, and only a confirmed outcome returns DELETED;
- secrets do not appear in captured logs/errors;
- CSP/CORS/Origin/CSRF guards are active, an unknown Host is rejected as a DNS-rebinding attempt;
- backup passes the SQLite integrity check.

## 5. Frontend/E2E

A fake `PlayerPort` reproduces ready/playing/buffering/paused/ended/error and a controllable position. Playwright checks:

- onboarding without OAuth;
- Wave start with one explicit action;
- queue restoration after reload;
- temperature changes only the future tail;
- offline/stale UI stays readable;
- rejected telemetry is retried without duplicating the accepted;
- like → pending sync → synced/error;
- keyboard navigation and visible focus;
- the iframe panel is not smaller than the minimum size on a supported desktop viewport;
- a tab going hidden does not create a negative reward and pauses the player only when `pause_on_hidden` is on;
- the destructive dialog describes the exact scope.

## 6. Real-account smoke

Performed manually with the `REAL_YTM_TESTS=1` flag:

1. `get_account_info` returns the expected account.
2. Read the first likes and one existing playlist without writing.
3. Get related/radio for one seed.
4. Play one `videoId` through the browser and receive state events.
5. Create a separate private playlist `Tuner Test <date>` with a single `create_playlist(..., video_ids=[id1, id2])`.
6. Read and verify both track IDs and their order.
7. Move the second item before the first via `moveItem=(setVideoId2, setVideoId1)` and re-verify the order.
8. Delete only this test playlist after an explicit check of the ID/marker.

The last step is destructive and is not part of the automatic default run. Managed production playlists are not used in the smoke test.

## 7. Acceptance scenarios

| ID | Scenario | Expected result |
| --- | --- | --- |
| AT-01 | Fresh Docker start | `/health/ready` green, UI on `127.0.0.1:43127`, data persistent |
| AT-02 | OAuth connect | account visible, tokens did not leak into UI/logs |
| AT-03 | Initial sync | likes/playlists cached, a screen refresh does not trigger new YTM calls |
| AT-04 | Play through UI | the visible iframe plays, custom controls and queue work |
| AT-05 | Early explicit next | correct played time and negative session reward |
| AT-06 | Buffer/close/hidden | no negative signal; with `pause_on_hidden` on, hidden additionally puts the player on policy pause |
| AT-07 | Bootstrap and baseline | up to 40 — rule ranker only; 40–99 — rule serving + SHADOW; ACTIVE is possible after a clean 100 and safety gates |
| AT-08 | Temperature | the first 20 items match the familiar/discovery quota and diversity |
| AT-09 | Autogeneration | 15 new sessions trigger training/new plan without user involvement; PARTIAL continues without a new signal after the cooldown |
| AT-10 | Safe publish | crash-safe create+verify with adaptive size; then only an ACTIVE managed playlist, gates, backup, convergent PARTIAL with item/request caps |
| AT-11 | Restart during job | no duplicate writes, state is restored |
| AT-12 | Official iPhone app | three private Tuner playlists are visible and playable in YouTube Music |

## 8. Quality thresholds before MVP

- 100% unit coverage for the ownership guard, diff cap and reward classification branches;
- at least 90% branch coverage of domain/recommender overall;
- zero high-severity dependency vulnerabilities or a documented exception;
- zero secrets according to the secret scanner;
- all AT-01…AT-12 passed and recorded with the date/app version;
- baseline and post-bootstrap metrics are computed on real sessions without manual substitution;
- a 24-hour soak test does not create extra sync/publish calls.

## 9. Post-launch observation

For the first two weeks it is better to keep auto-publish in preview-only mode, or to enable it after a manual check of each plan. The following are checked:

- false skips due to UI/player transitions;
- exceeding the call budget;
- repeated parse errors;
- artist concentration;
- playlist verification mismatch;
- the distribution of `rule_score` and the share of candidates passing `rule_score >= 0.40`, separately for Familiar/Balance/Discovery; this is a diagnostic of an uncalibrated mapping, not grounds for automatically changing the threshold;
- WAL size and the success of the daily backup.
