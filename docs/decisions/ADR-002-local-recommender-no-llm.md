# ADR-002: local recommender without a mandatory LLM

- Status: Accepted
- Date: 2026-08-01
- Scope: recommendation/AI

## Context

A choice is needed between an external LLM, a small local model and an ordinary ML library. At the start there is a catalogue/recommendation graph and a small feedback stream from one person; there is no full audio signal and no large labelled dataset.

## Options considered

### External LLM

Pros: understands free text, can explain well. Cons: the history leaves the computer, cost/latency, weak suitability for numeric online ranking, no durable memory without a separate feature store.

### Local general-purpose LLM

Pros: privacy and a text interface. Cons: model server, memory/image, still not the right inductive bias for ranking; an explanation does not guarantee the right choice of track.

### Audio embedding model

Pros: real acoustic similarity. Cons: requires lawful access to audio, large computational/operational cost; ytmusicapi is not a source of audio files for analysis.

### Classic local recommender/contextual bandit

Pros: learns from small online feedback, milliseconds of CPU, transparent, reproducible, easily controls exploration. Cons: requires careful features and safeguards; no magical understanding of text/audio.

## Decision

Use a hybrid:

1. candidate generation from cached related/radio/mood sources of YouTube Music;
2. a fixed rule-based ranker for a clean baseline of 100 sessions;
3. a local LinUCB/contextual bandit on NumPy in SHADOW after bootstrap 40 and serving only after 100 sessions/safety gates;
4. deterministic diversity/fatigue reranker;
5. temperature controls quota and uncertainty bonus.

Serving ownership is stored explicitly: BASELINE/SHADOW is served by `rule-score-v1` with a nullable shadow model, ACTIVE by a specific LinUCB snapshot. For the shared playlist gates, the rule score is mapped to `quality_expected=2*rule_score-1`, while ACTIVE uses LinUCB exploitation `theta^T x`; uncertainty/exploration does not participate in the quality floor.

No LLM is a runtime dependency or receives telemetry.

## Consequences

- Docker stays lightweight and runs without a GPU/model download.
- The model can be explained with real reason codes.
- Raw telemetry stays local.
- Improvement depends on the quality of events and the candidate pool, not on the size of a language model.
- Later, small metadata embeddings or an optional text-to-mood parser can be added as enrichment, without changing the core ranker.

## Revisit conditions

- after 500+ qualified sessions, offline/online metrics show a plateau;
- candidate diversity is insufficient because of weak metadata features;
- a lawful and stable source of audio features appears;
- the user explicitly wants natural-language mood input and agrees to a local model runtime.
