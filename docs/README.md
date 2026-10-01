# Project documentation set

The documents describe the target state of YouTube Music Tuner. The wording "must" denotes a requirement for the implementation; the actual development status is reflected only in the root `README.md` and the roadmap.

Starting with revision 1.3, a significant part of the documents describes an already working system rather than a plan: the numbers given in the text were measured on a live library and are dated.

## Revision 1.3 — 2026-08-03

The first weeks of real use revealed that the selection was weaker than described, and the weaknesses turned out to be structural rather than a matter of tuning.

**Recommender.** Nobody populated the taste aggregates, so the listening history did not affect what was served at all. The candidate pool was discarded by TTL and did not accumulate, the multiplicity of edges collapsed, and the graph traversal deepened one cluster, leaving most of the library unrepresented. All of this has been reworked: the graph became persistent and multi-hop, breadth comes before depth, and wave freshness became an explicit constraint with memory at the track and artist level. The decision is recorded in ADR-004.

**Publishing.** The preview showed only counters, that is, publishing was done blind; the playlist contents could be neither seen, nor heard, nor regenerated, and a ready playlist could not be deleted. Now viewing the list is mandatory and substantive, and the safety boundary has shifted from "record status" to "ownership marker match".

**Infrastructure.** Three failures discovered in operation have been eliminated: the scheduler's tight loop, telemetry loss caused by the single write lock of SQLite, and the inability to verify a freshly created playlist.

**Reversed decision.** The pause on a hidden tab is **off** by default by direct decision of the installation owner (2026-08-01), reversing the decisions of revision 1.1. The residual risk is accepted and recorded in docs/10.

## Revision 1.2.1 — 2026-08-01

Cleanup of an unverified setup artifact has been brought to the level of an integration contract: added `delete_managed_playlist`/`delete_playlist`, an exact scope, a separate two-request budget, a transition to DELETED and contract fixtures. The rule-score mapping is explicitly designated as an uncalibrated monotonic transformation; the first real preview must measure the pass rate of the 0.40 threshold.

## Revision 1.2 — 2026-08-01

A repeated review closed early publication without an ACTIVE model through the shared `quality_expected`, an adaptive size of 25–60 and exact pool diagnostics, a crash-safe CREATING/UNVERIFIED lifecycle, continuation of PARTIAL without a repeated listening threshold, independent item/request budgets and an unambiguous BASELINE/SHADOW/ACTIVE API. Also clarified: qualification of a short explicit Next, reward precedence, proportional artist diversity and retention of feature snapshots.

## Revision 1.1 — 2026-08-01

After an end-to-end architectural review the following were clarified: clean baseline/SHADOW activation, reward normalisation, `progress_tick`, unknown-duration/seek classification, revision-aware rating jobs, initial playlist fill, versioned playlist quality gates, convergent PARTIAL ordering, single-owner diversity rules, named-volume UID/GID, CSRF and DNS-rebinding protection. The policy pause on hidden was kept on the basis of the current YouTube Developer Policies — the decision was reversed in revision 1.3.

## Reading order

1. [Product requirements](01-product-requirements.md) — why the product exists and what is included in the MVP.
2. [System architecture](02-system-architecture.md) — components, boundaries and data flow.
3. [YouTube Music integration](03-youtube-integration.md) — `ytmusicapi`, OAuth, synchronisation and limits.
4. [Player and telemetry](04-player-and-telemetry.md) — how actual listening is measured.
5. [Recommendation engine](05-recommendation-engine.md) — candidate generation, training and temperature.
6. [UI/UX](06-ui-ux.md) — information architecture and visual principles.
7. [Data model](07-data-model.md) — tables, identifiers and storage.
8. [Internal API](08-api-contract.md) — the frontend/backend contract.
9. [Docker and operations](09-operations-docker.md) — launch, health check, backup and restore.
10. [Security, privacy and risks](10-security-privacy-risks.md).
11. [Testing and acceptance](11-testing-acceptance.md).
12. [Roadmap](12-roadmap.md).
13. [Sources](13-references.md) — the documentation studied and the date it was checked.

## Architecture Decision Records

- [ADR-001: use ytmusicapi](decisions/ADR-001-use-ytmusicapi.md);
- [ADR-002: local recommender without a mandatory LLM](decisions/ADR-002-local-recommender-no-llm.md);
- [ADR-003: one container and port 43127](decisions/ADR-003-single-container-port.md);
- [ADR-004: candidate graph instead of a cached pool](decisions/ADR-004-candidate-graph-over-cached-pool.md).

## Maintenance rules

- Change proposals are managed through OpenSpec: `openspec/changes/` contains the active proposals, `openspec/specs/` the living specifications accumulated from completed changes. The project context for the assistant is set in `openspec/config.yaml`. These documents remain the source of truth for requirements; OpenSpec records the path from proposal to implementation.
- When product behaviour changes, the corresponding requirement and acceptance criterion are updated first.
- When a fundamental technical decision changes, a new ADR is added that replaces the old one; the old ADR is not deleted.
- The `ytmusicapi` version, the OAuth process and the supported methods are rechecked before every dependency update.
- A document must not contain OAuth tokens, a Google client secret, cookies or real identifiers of private playlists.
