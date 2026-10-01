# Sources studied

Verification date: 2026-08-01.

## ytmusicapi

- [GitHub repository](https://github.com/sigma67/ytmusicapi) — unofficial API positioning, capabilities, activity.
- [PyPI project](https://pypi.org/project/ytmusicapi/) — version `1.12.1`, release date 2026-06-05, Python requirement and release history.
- [Stable documentation 1.12.1](https://ytmusicapi.readthedocs.io/en/stable/) — current user documentation.
- [Setup](https://ytmusicapi.readthedocs.io/en/stable/setup/index.html) — installation and auth options.
- [OAuth authentication](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html) — own client ID/secret, type `TVs and Limited Input devices`, `oauth.json`.
- [API reference](https://ytmusicapi.readthedocs.io/en/stable/reference/index.html) — library, history, watch/radio, moods and playlists methods.
- [Playlist reference 1.12.1](https://ytmusicapi.readthedocs.io/en/stable/reference/playlists.html) — initial `video_ids`, item identity `setVideoId` and `edit_playlist(moveItem=...)` for ordering.
- [Releases](https://github.com/sigma67/ytmusicapi/releases) — features/fixes and contributors.
- [Latest commits via GitHub API](https://api.github.com/repos/sigma67/ytmusicapi/commits?per_page=5) — live check of `main` activity.
- [Latest development documentation](https://ytmusicapi.readthedocs.io/en/latest/) — the ongoing dev version after the stable release.

The GitHub API check on 2026-08-01 showed fresh commits in `main` from 2026-07-25, including a parser fix, test parallelization and an update of the lint/type-check infrastructure. This is operational evidence for the decision, but the lockfile still pins a release, not a commit from `main`.

## YouTube

- [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference) — player creation/control, events, `origin`, minimum viewport size.
- [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) — policy boundary, prohibition of undocumented APIs and of a background player outside the viewed page/tab/screen.
- [Required Minimum Functionality](https://developers.google.com/youtube/terms/required-minimum-functionality) — requirements for embedded playback/client behaviour.

## Recommender systems

Studied on 2026-08-01 while reworking candidate generation (ADR-004).

- [A Comprehensive Survey on Retrieval Methods in Recommender Systems](https://arxiv.org/pdf/2308.01204) — the three-stage funnel candidate generation → ranking → re-ranking and the role of the retrieval stage.
- [Result Diversification in Search and Recommendation: A Survey](https://arxiv.org/pdf/2212.14464) — MMR versus DPP, myopia of greedy selection.
- [Determinantal Point Processes for diversity](https://medium.com/data-science-collective/diversity-in-recommendations-determinantal-point-processes-dpp-2427bf1b6324) — windowed DPP, window sizes of 6–12 in product feeds.
- [Item2Vec](https://www.emergentmind.com/papers/1603.04259) — embeddings from co-occurrence, without audio analysis.
- [Item-Graph2vec](https://arxiv.org/pdf/2310.14215) — a co-occurrence graph as a development of Item2Vec.
- [A Random Walk Model for Item Recommendation](https://arxiv.org/pdf/1310.7957) and [Boosting Item-based CF via Nearly Uncoupled Random Walks](https://arxiv.org/pdf/1909.03579) — walking a similarity graph as candidate generation.
- [Spotify Sequential Skip Prediction Challenge](https://www.aicrowd.com/challenges/spotify-sequential-skip-prediction-challenge) and [RNN approach](https://arxiv.org/pdf/1904.10273) — skip as a first-class signal.
- [Calibrated Recommendations with Contextual Bandits](https://research.atspotify.com/2025/9/calibrated-recommendations-with-contextual-bandits-on-spotify-homepage) — a composition quota as calibration, not as a tie-breaker.
- [Contextual and Sequential User Embeddings (CoSeRNN)](https://research.atspotify.com/2021/04/contextual-and-sequential-user-embeddings-for-music-recommendation) — predicting preferences at session start.
- [Efficient Exploration and Exploitation for Sequential Music Recommendation](https://dl.acm.org/doi/10.1145/3625827) — why purely random exploration converges slowly.
- [Interview with the Yandex Music My Wave team](https://the-flow.ru/features/yandeks-moya-volna-intervyu) (in Russian) — three classes of algorithms, adjusting on every track, weighting signals by significance.

## Docker

- [Docker volumes](https://docs.docker.com/engine/storage/volumes/) — named volume lifecycle, default copy/pre-population of an empty volume and backup/restore guidance.

## How to apply the sources

- The `ytmusicapi` docs define the real supported adapter interface.
- The official YouTube documentation takes priority for playback and UI constraints.
- No source guarantees the stability of internal YouTube Music endpoints; therefore caching, adapter tests, a conservative call budget and rollback are mandatory.
- Before implementation starts and at every dependency upgrade, the stable version, setup/OAuth changes and repository activity must be re-checked.
