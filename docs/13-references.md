# Изученные источники

Дата проверки: 2026-08-01.

## ytmusicapi

- [GitHub repository](https://github.com/sigma67/ytmusicapi) — позиционирование unofficial API, возможности, activity.
- [PyPI project](https://pypi.org/project/ytmusicapi/) — версия `1.12.1`, дата релиза 2026-06-05, Python requirement и release history.
- [Stable documentation 1.12.1](https://ytmusicapi.readthedocs.io/en/stable/) — актуальная пользовательская документация.
- [Setup](https://ytmusicapi.readthedocs.io/en/stable/setup/index.html) — установка и варианты auth.
- [OAuth authentication](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html) — собственный client ID/secret, тип `TVs and Limited Input devices`, `oauth.json`.
- [API reference](https://ytmusicapi.readthedocs.io/en/stable/reference/index.html) — library, history, watch/radio, moods и playlists methods.
- [Playlist reference 1.12.1](https://ytmusicapi.readthedocs.io/en/stable/reference/playlists.html) — initial `video_ids`, item identity `setVideoId` и `edit_playlist(moveItem=...)` для порядка.
- [Releases](https://github.com/sigma67/ytmusicapi/releases) — features/fixes и contributors.
- [Последние commits через GitHub API](https://api.github.com/repos/sigma67/ytmusicapi/commits?per_page=5) — live-проверка активности `main`.
- [Latest development documentation](https://ytmusicapi.readthedocs.io/en/latest/) — продолжающаяся dev-версия после stable release.

Проверка GitHub API 2026-08-01 показала свежие commits в `main` от 2026-07-25, включая parser fix, test parallelization и обновление lint/type-check инфраструктуры. Это operational evidence для решения, но lockfile всё равно фиксирует release, а не commit из `main`.

## YouTube

- [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference) — создание/управление player, события, `origin`, минимальный размер viewport.
- [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) — policy boundary, запрет undocumented API и background player вне просматриваемой страницы/вкладки/экрана.
- [Required Minimum Functionality](https://developers.google.com/youtube/terms/required-minimum-functionality) — требования к embedded playback/client behavior.

## Docker

- [Docker volumes](https://docs.docker.com/engine/storage/volumes/) — lifecycle named volumes, default copy/pre-population пустого volume и backup/restore guidance.

## Как применять источники

- `ytmusicapi` docs определяют реальный поддерживаемый интерфейс adapter.
- Официальная YouTube документация имеет приоритет для playback и UI constraints.
- Ни один источник не гарантирует стабильность внутренних YouTube Music endpoints; поэтому caching, adapter tests, conservative call budget и rollback обязательны.
- Перед началом реализации и каждым dependency upgrade нужно повторно проверить stable version, setup/OAuth changes и repository activity.
