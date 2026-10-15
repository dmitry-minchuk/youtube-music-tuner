# ADR-005: explicit regenerate replaces a playlist in one action

- Status: Accepted
- Date: 2026-10-15
- Scope: managed playlist publishing, playlist UI

## Context

The owner listens to the Tuner playlists in the YouTube Music app on the phone and dislikes tracks there. On 2026-10-15 a read of the three playlists showed 6, 13 and 16 tracks with `likeStatus=DISLIKE` — 18 distinct tracks — and none of them was disliked locally: Tuner never read ratings back, so every regeneration put them into the playlists again.

The playlist card offered `Preview`, `Refresh content` and `Regenerate`. `Regenerate` only proposed a new local list; `Refresh content` wrote the reviewed list incrementally, 15 item changes per 24-hour window, so replacing a 60-track list took about four days and four presses, and an unfinished PARTIAL kept writing the old list even after a new one was generated. The owner could not tell which button changes YouTube.

Owner's requirements (2026-10-15):

1. A track the owner disliked must never be in a Tuner playlist.
2. It must be clear which button regenerates the playlist and posts the changed playlist to YouTube.
3. Every regeneration must take all behaviour into account — likes, dislikes and the rest.

## Decision

1. Ratings set in YouTube Music are read back from every `get_playlist` and `get_history` response and applied locally (docs/03 section 6). The watch/related/search/liked payloads are not used for this: the watch payload reports a dislike as `INDIFFERENT`.
2. A dislike also blocks the same song under another `videoId` (same normalised title and primary artist), because YouTube may store a written track under a canonical id.
3. For an ACTIVE playlist there is one confirmed action, `Regenerate & update on YouTube`: fresh read of this and the other Tuner playlists with rating ingest → a new list from all local behaviour → one batch add of the new list (`duplicates=True`) → one batch remove of all previous items → verification read → one more remove if the verification shows a written track as disliked (docs/03 section 8). The preview step, the 15-item cap and the 24-hour cooldown do not apply to it; the request budgets (17 per playlist per rolling 24 hours, 51 per day), the circuit breaker and the ownership marker do.
4. Add before remove: a failed add changes nothing, a failed remove leaves old + new and the next press cleans it up. The playlist is never empty. Verified live on 2026-10-15 on a throwaway private playlist: the add appended the list in exactly the given order including duplicates, and removing the previous `setVideoId`s left exactly the new list.

## Consequences

- The owner sees the posted list right after the action, not before it. The previous contents are kept in a backup; restoring one is still a manual, planned operation, not an undo button.
- One press costs 2–3 mutating requests on the playlist, up to 3 reads of it and one read of each other Tuner playlist, which allows two to three presses per playlist per day.
- A dislike of a track that has never appeared in a Tuner playlist or in the history cannot be known before that track is written; the verification read reveals it and the track is taken out in the same action.
- The incremental `Refresh` path stays in the backend for the API but is no longer offered for an ACTIVE playlist in the UI.

## Revisit conditions

- YouTube starts rejecting batch adds of 60 tracks or changes the order of a batch add.
- The request budget turns out to be too tight for the owner's real use.
