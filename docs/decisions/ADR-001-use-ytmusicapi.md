# ADR-001: use ytmusicapi

- Status: Accepted
- Date: 2026-08-01
- Scope: YouTube Music integration

## Context

Tuner must read likes, playlists, history and recommendation sources, and also create/modify private playlists. The official YouTube Data API does not provide an equivalent of the whole YouTube Music web API. For a personal tool, the user is explicitly willing to use an unofficial integration if the library is current and under active development.

## Verified facts

- PyPI contains `ytmusicapi 1.12.1`, published on 2026-06-05 via Trusted Publishing.
- In 2026 there were releases `1.11.5`, `1.12.0`, `1.12.1`; before that, a sequence of releases came out in 2025.
- The stable docs correspond to `1.12.1`; the latest docs already have the development version `1.12.2.dev...`.
- `main` had commits on 2026-07-25: parser fix, tests, lint/type-check maintenance.
- Release 1.12.0 included new playlist/song-credit capabilities, parser/browser fixes, documentation and several contributors.
- The reference covers `get_liked_songs`, `get_history`, `get_song_related`, `get_watch_playlist`, moods, library and playlist mutations.
- The project explicitly calls itself `Unofficial API for YouTube Music`.

Conclusion: as of the decision date the library is alive, current and actively maintained; it can be used for this personal-use scope.

## Decision

Use `ytmusicapi==1.12.1` in the first implementation and pin the exact version in the lockfile.

Encapsulate the library in `YouTubeMusicAdapter`, which implements the internal `MusicCatalogPort`. Domain, recommender and UI do not work with the library's raw dictionaries.

Do not implement playback through ytmusicapi or by extracting stream URLs. Use the official YouTube IFrame Player.

## Consequences

Positive:

- the required YouTube Music operations are available right now;
- the Python API is a good fit for the FastAPI backend;
- there is documentation, tests and active fixes;
- no custom reverse-engineering client is needed.

Negative:

- an internal endpoint may change without backward compatibility;
- Google does not officially support this library;
- individual methods may break independently of the Tuner version;
- using undocumented APIs carries policy/account risk even for a personal scenario.

## Mandatory measures

- pinned dependency and reproducible image;
- adapter contract tests;
- conservative TTL/call budget/backoff/circuit breaker;
- no polling;
- cached read-only degradation;
- write only to managed playlists with backup/diff/verify;
- a separate real smoke test before enabling write after an upgrade;
- dependency health review at least once a quarter or on breakage.

## Real-account check (2026-08-01)

Device flow with a self-made OAuth client completes successfully, but all internal API calls return `HTTP 400 Bad Request`; unauthenticated `search` works. Therefore browser/cookie was chosen as the working authentication method, and OAuth was kept as a fallback. The decision to use `ytmusicapi` itself stands: the library and its parsers work correctly, the limitation is on Google's side.

## Revisit conditions

Revisit the decision if there is no compatible release/fix more than 90 days after a confirmed breakage, if OAuth becomes unacceptably risky, or if the official API begins to cover the required YouTube Music operations.

## Sources

- [PyPI](https://pypi.org/project/ytmusicapi/)
- [GitHub](https://github.com/sigma67/ytmusicapi)
- [Latest commits](https://api.github.com/repos/sigma67/ytmusicapi/commits?per_page=5)
- [Stable reference](https://ytmusicapi.readthedocs.io/en/stable/reference/index.html)
- [OAuth setup](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html)
- [Releases](https://github.com/sigma67/ytmusicapi/releases)
