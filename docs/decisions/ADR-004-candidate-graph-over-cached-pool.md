# ADR-004: candidate graph instead of a cached pool

- Status: Accepted
- Date: 2026-08-01
- Scope: recommendation/candidate generation

## Context

After two weeks of real use, Restart Wave began to serve the same tracks the user had just skipped. Measurements on the live database:

| Metric | Value |
| --- | --- |
| Overlap of two adjacent waves | 38 of 40 tracks (95%) |
| Unique candidates in the pool | 185, of which 119 playable |
| Distinct tracks already listened to | 141 — more than the entire playable pool |
| Rows in `track_affinity` / `artist_affinity` | 0 / 0 |
| Early skips that affected the output | 0 of 91 |
| Seeds per run | 4, all from 17 playable likes |

The causes turned out to be structural, not a matter of tuning.

**The pool did not grow.** `seed → candidate` edges lived 7 days and were then deleted, and `_has_fresh_edges` prohibited re-querying a seed that had fresh edges. The same 4 seeds gave the same answer; by construction, no knowledge accumulated.

**Link multiplicity was discarded.** All edges to a candidate were collapsed into one "best by rank" edge, and the constant `distinct_seed_count=1` went into the feature vector.

**The feedback loop was open.** Nobody populated the aggregate tables, so `fatigue`, `recent_skip`, `novelty`, `rediscovery` were identically zero. Of the five weights of `rule-score-v1`, exactly one worked.

## What the theory says

The industry standard is a three-stage funnel candidate generation → ranking → re-ranking, where each stage works only with what the previous one passed on. A good ranker on top of a poor pool gives a poor result.

Yandex's "My Wave" combines three classes of algorithms: collaborative, content-based ("audio fingerprint") and statistics, blending them with CatBoost, and it adapts on every track, not once a day. The pool is 80 million tracks.

For a one-person setup, classic collaborative filtering is impossible: there are no "users similar to you". But YouTube's `related`/`radio` **are** the result of collaborative filtering computed over millions of listeners. This is the collaborative layer available to us, and we used it one hop away from the four seeds.

We do not have a content layer (audio) and will not — the API does not provide audio features. Its legitimate substitute is co-occurrence in the same graph: item-embedding methods (Item2Vec, Item-Graph2vec) learn similarity precisely from joint occurrence, not from a signal. Traversing a similarity graph with a random walk is a standard way to generate candidates.

For diversity, MMR is myopic; windowed DPP rerankers give better diversity without losing accuracy (YouTube uses windows of 6–12).

## Decision

1. **Graph instead of cache.** Edges accumulate indefinitely; `expires_at` only means "the seed may be queried again". Each edge carries `hop` — the distance from a positive root, the contribution is discounted as `0.72^(hop-1)`, depth up to 3.
2. **Frontier expansion.** Every 6 hours, up to 8 of the most-supported unexpanded nodes are queried via radio (1 call, up to 25 candidates). Priority is by the number of incoming edges from favourite tracks; nodes with a negative reward are not expanded.
3. **Co-occurrence as a feature.** `distinct_seed_count` is computed for real: how many different positive roots point to the candidate. This is a direct analog of the co-occurrence count in item-embedding methods.
4. **Closed loop.** Aggregates are updated immediately after the session is re-aggregated, by full recomputation per track (idempotent under repeated delivery of a batch).
5. **Freshness as a constraint, not a side effect of ranking.** An adaptive novelty window, memory of the four most recent waves, stochastic selection from the 12 leaders instead of argmax, a cap on the share of the pool that gets used up.

## Consequences

Measurements after the rollout on the same database:

| Metric | Before | After |
| --- | --- | --- |
| Overlap of adjacent waves | 95% | 0–25% |
| Repeat of discovery tracks | almost complete | 0% |
| Unique candidates | 185 | 404 after one expansion run |
| Playable candidates | 119 | 340 |
| Aggregate rows | 0 | 141 tracks, 124 artists |
| Candidates supported by 2+ favourite seeds | not counted | 34 |

The cost is an increase in the daily budget of discovery calls to 60 (it was effectively ~15) and growth of the DB: the graph grows by about 800 edges per day, and retention purges edges older than a year.

A conflict between two requirements emerged: the familiar quota at temperature 50 asks for 22 familiar tracks out of 40, but there are only 18 playable favourites in total — they would have to be repeated in full. Priority was given to freshness: a wave takes no more than 75% (cold) to 50% (hot) of the available familiar tracks, and a quota shortfall is flagged with the code `FAMILIAR_ROTATION_CAP`. The real solution is more playable likes, but 35 of 52 are blocked from embedding on YouTube's side.

## Revisit conditions

- the graph grows beyond ~200k edges and loading it in full for every wave no longer fits the time budget — then switch to SQL aggregation or an incremental support index;
- the pool becomes so large that the rotation cap is no longer needed;
- a lawful source of audio features appears — then co-occurrence is complemented with a content layer, as at Yandex;
- LinUCB moves to ACTIVE and starts managing exploration itself — some of the freshness heuristics may give way to the uncertainty bonus.
