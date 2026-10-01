# UI/UX

## 1. Direction

The interface takes the strengths of Yandex Music as a reference — a calm hierarchy, minimal visual noise, quick entry into the personal flow, large covers and a persistent player. Branding, the exact screen composition, colours, icons and microcopy are not copied.

Own visual identity:

- dark graphite background, not pure black;
- `warm coral` accent for the primary action and `cool teal` for discovery/temperature;
- soft surfaces without excessive borders;
- generous spacing and one main goal per screen;
- system font, no external font dependency in the MVP.

## 2. Information architecture

Left menu:

- `Wave` — the main screen;
- `Collection` — likes and locally known tracks;
- `Playlists` — remote and managed playlists;
- `Insights` — learning, signals and quality;
- `Settings` — connection, sync, automation, privacy.

Persistent areas:

- central content;
- a visible YouTube player area of at least 200×200;
- bottom player bar;
- a compact sync/API status indicator only when there is a problem or an active operation.

## 3. Desktop wireframe

```text
┌──────────────┬──────────────────────────────────────┬────────────────────┐
│ TUNER        │ Good evening                         │ Now playing        │
│              │                                      │ ┌────────────────┐ │
│ ● Wave       │  Your Wave                           │ │ YouTube iframe │ │
│   Collection │  [ Focus ▾ ]                         │ │  visible       │ │
│   Playlists  │                                      │ └────────────────┘ │
│   Insights   │  Familiar ━━━━━●━━━━ Discovery       │ Title              │
│   Settings   │                                      │ Artist             │
│              │  [▶ Start Wave]  [Why this mix?]     │ Why: rediscovery   │
│              │                                      │                    │
│              │  Up next                             │ Learning 73 events │
│              │  01 Track — Artist        3:42       │                    │
│              │  02 Track — Artist        4:03       │                    │
└──────────────┴──────────────────────────────────────┴────────────────────┘
┌──────────────────────────────────────────────────────────────────────────┐
│ ♡  artwork  Title — Artist       ◀  ▶/Ⅱ  ▶       ━━━━━━━  🔊   ···      │
└──────────────────────────────────────────────────────────────────────────┘
```

At a width of 1024–1279 px the right panel turns into an expandable Now Playing drawer, but the iframe stays available and of sufficient size during playback.

## 4. Wave

The first viewport contains only:

- greeting/context;
- mood selector;
- temperature slider labelled `Familiar` and `Discovery`;
- one primary button, `Start Wave` or `Resume`;
- the first `Up next` items;
- a short learning/status label.

The temperature slider shows the expected composition, for example `55% familiar · 45% discovery`, not an abstract number. A change does not rebuild the history that has already been played, only the future tail.

Every track has `Why this?` available, with 1–3 computed reasons and no generated text:

- `Liked artist, new track`;
- `Related to …`;
- `You usually finish this artist`;
- `Not played for 8 months`;
- `Discovery pick with high uncertainty`.

In the player, next to `Like`/`Dislike`, there is a third button, `Not my thing` ("Don't Like At All", docs/05 §11): it sets a local veto on the track, its artist and its graph neighbourhood and immediately switches to the next track. Pressing it again lifts the veto. Unlike `Dislike`, the signal is never sent to YouTube.

The severity hierarchy is distinguishable visually, not only by text: `Dislike` is a neutral button with a small cross in a circle (one track crossed out), `Not my thing` carries a danger tone even at rest and a fully struck-through circle (a direction closed off); the pressed state is a coral fill with a glow. The weight of the button is itself the warning about how strong the action is.

`Dislike` also switches to the next track immediately, and any negative signal quietly rebuilds the unplayed tail of the queue (docs/04 §8) — the next tracks already account for the attitude just expressed.

## 5. Collection

- tabs `Liked`, `Recently played`, `Discovered`, `Blocked`;
- search/filter works over the local catalogue;
- rows show local affinity, last played and sync state only on request via `Details`, so as not to overload the main list;
- bulk editing of the external library is not part of the MVP.

## 6. Playlists

Two sections:

- `Tuner playlists` — Familiar, Balance, Discovery with lifecycle status, configured/effective size, the content freshness date and the buttons Preview/Refresh content/Regenerate/Delete. Creating a playlist immediately fills it with the full selection — "publish" in the sense of "make public" does not exist (the playlists are private and are visible to the owner in YouTube Music immediately). The button is called `Refresh content` and means the one thing it does: rebuild the selection for the current taste and roll the difference onto the already live playlist. The card shows `Contents from <date>`, where creation counts as the first release: "Never published" right after creation hinted at unfinished work that does not exist. The set of actions depends on the state: ACTIVE — Refresh content/Delete playlist; UNVERIFIED, CLEANUP_REQUIRED and CREATING with a remote ID — Verify/adopt + Delete setup artifact; CREATING without a remote ID — Create on YouTube + Delete setup artifact. A deleted playlist disappears from the list (that is the confirmation of the deletion), and a button for creating the missing ones appears; the result of Verify/adopt is always shown in words, and a stuck setup by its own `setupErrorCode`. A playlist deleted on YouTube may remain in the "Your YouTube Music playlists" list until the next library sync — this is a cache;
- `Your YouTube Music playlists` — read-only cards and the `Play in Tuner` action. The size is shown as is: YouTube does not report it for its system playlists, and such a card says "size not reported by YouTube", not "0 tracks". For Liked Music the locally known number of likes is substituted.

### Preview is a view, not a counter

Preview reveals **the list itself**: position, title, artist and a `known`/`new` tag. Any row can be clicked to hear it in the regular player, and the `Play` button queues the whole list. The list shown is fixed, and `Publish now` writes exactly that list; until Preview has been opened, the publish button is inactive. `Regenerate` offers a different variant.

Previously there were only counters here, which meant publishing was done blind — this contradicted its own promise "never touched without a preview".

Before a manual publish, the additions/removals/moves, the results of the playlist quality gates, the target generation and the remaining item changes/HTTP requests for a PARTIAL publication are shown. If the target has been reduced because of the pool, the UI says so explicitly, for example `42 of configured 60 · limited by 30 familiar tracks`, with the reason `TARGET_SIZE_REDUCED_FOR_POOL`. On `INSUFFICIENT_POOL` the required/available counts are shown together with the actions: lower the temperature, reduce the configured size, or accumulate more likes/listens. The blocking reason is displayed separately if quality, cooldown or the API circuit does not allow the write.

CREATING/UNVERIFIED/CLEANUP_REQUIRED are not disguised as a ready playlist. `Verify/adopt` and confirmed deletion are available for them; regular Publish/Restore are unavailable until ACTIVE. Deletion is available for ACTIVE as well: it is the listener's playlist, and a variant they do not like must be removable from the application. In both cases the dialog shows the exact remote ID, and the server re-checks the ownership marker.

A refusal at the creation stage is shown for each playlist separately and in human-readable text. A refusal by the quality gates is returned as a regular `200`, so UI silence is unacceptable here: previously, pressing "Create the three playlists" looked like no reaction at all.

## 7. Insights

Do not turn the screen into an ML dashboard. Show by default:

- `Collecting signal 31/40 qualified tracks`, then `Baseline 73/100 · model in shadow`, or `Model active`; the tooltip explains that these are listened tracks/explicit reactions, not application launches;
- the change in early skip and completion relative to the baseline, with sample size;
- top positive/negative artists based only on local behaviour;
- familiar/new over 7 and 30 days;
- the last/next automatic generation;
- `What Tuner learned` as deterministic facts.

The `Discovery pool` panel shows what a wave is built from in the first place: the number of playable candidates, the size of the graph, the distribution by distance from the favourites ("N at 1 step, M at 2 steps") and the percentage of the previous wave that is repeated. These are the two quantities the graph exists for: a pool that grows, and waves that do not repeat.

The extended diagnostics drawer contains the model version, event counts, the call ledger and the consumption of the daily budget of discovery calls.

## 8. Settings

Groups:

- `YouTube Music`: account, OAuth status, reconnect, last sync;
- `Automation`: auto-train, auto-publish, publish window, default temperature;
- `Playback`: pause-when-hidden (**off** by default — the owner's decision of 2026-08-01, see docs/04 section 1), default temperature, volume, repeat default;
- `Privacy`: event retention, export summary, delete telemetry, disconnect;
- `Diagnostics`: health, database path/size, dependency version, circuit breaker, logs download without secrets.

Dangerous actions are visually separated and require explicit confirmation with an exact description of the consequences.

## 9. States

Every data screen has four explicit states:

- loading skeleton on the first local read;
- empty state with a single next action;
- stale-but-usable, with the time of the last sync;
- error with a retry that respects the cooldown.

If YouTube is unavailable, the existing local queue and Insights remain available. Red is used only for errors and destructive actions, not for the ordinary dislike control.

## 10. Accessibility

- all controls are keyboard accessible;
- visible focus ring;
- WCAG AA contrast for text/controls;
- temperature is operable with arrow keys and has `aria-valuetext`;
- icons have accessible labels;
- the like/dislike state is not conveyed by colour alone: an active rating carries a filled glyph, a border, `aria-pressed` and a sync status caption (`syncing…`/`synced`/`not synced`);
- every action gets immediate feedback: an optimistic button state, `:active` feedback, `disabled` while the player is not ready, a `retuning…` indicator while the tail of the queue is being rebuilt;
- an operation that takes noticeable time (preview of the whole pool, create/publish/verify/delete with external calls) shows a progress indicator with a caption saying what exactly is happening; a frozen button with no motion reads as a hang. The bar is honestly indeterminate: these are single HTTP calls with no intermediate percentages;
- animations are disabled under `prefers-reduced-motion`;
- hit target of at least 40×40 px.

## 11. Copy and tone

A short, calm and honest interface:

- `Learning from 31 qualified listens` instead of `AI is analyzing you`;
- `YouTube Music sync paused — reconnect required` instead of a generic `Something went wrong`;
- `No negative signal recorded` when the page is closed;
- `Playback paused because this tab is no longer visible` on a policy pause;
- for a new plan: `Next automatic publish after 21:00, if enough new listens`;
- for an unfinished plan: `Continuing 9 remaining changes after 21:00 · no new listens required`.

The UI does not promise to know the user's mood and always shows when the context was chosen manually.
