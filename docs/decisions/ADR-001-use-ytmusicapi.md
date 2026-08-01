# ADR-001: использовать ytmusicapi

- Статус: Accepted
- Дата: 2026-08-01
- Область: YouTube Music integration

## Контекст

Tuner должен читать лайки, плейлисты, историю и источники рекомендаций, а также создавать/изменять приватные плейлисты. Официальный YouTube Data API не предоставляет эквивалент всего YouTube Music web API. Для личного инструмента пользователь явно готов использовать неофициальную интеграцию, если библиотека актуальна и развивается.

## Проверенные факты

- PyPI содержит `ytmusicapi 1.12.1`, опубликованную 2026-06-05 через Trusted Publishing.
- В 2026 были релизы `1.11.5`, `1.12.0`, `1.12.1`; до этого выходила последовательность релизов в 2025.
- Stable docs соответствуют `1.12.1`, latest docs уже имеют development version `1.12.2.dev...`.
- В `main` были commits 2026-07-25: parser fix, тесты, lint/type-check maintenance.
- Release 1.12.0 включал новые playlist/song-credit возможности, parser/browser fixes, документацию и нескольких contributors.
- Reference покрывает `get_liked_songs`, `get_history`, `get_song_related`, `get_watch_playlist`, moods, library и playlist mutations.
- Проект прямо называет себя `Unofficial API for YouTube Music`.

Вывод: на дату решения библиотека живая, актуальная и активно поддерживаемая; для данного personal-use scope её можно использовать.

## Решение

Использовать `ytmusicapi==1.12.1` в первой реализации и фиксировать exact version в lockfile.

Инкапсулировать библиотеку в `YouTubeMusicAdapter`, который реализует внутренний `MusicCatalogPort`. Domain, recommender и UI не работают с raw dictionaries библиотеки.

Воспроизведение не реализовывать через ytmusicapi или извлечение stream URL. Использовать официальный YouTube IFrame Player.

## Последствия

Положительные:

- нужные YouTube Music операции доступны уже сейчас;
- Python API хорошо подходит FastAPI backend;
- есть документация, tests и активные исправления;
- не нужен собственный reverse engineering client.

Отрицательные:

- внутренний endpoint может измениться без backward compatibility;
- Google официально не поддерживает эту библиотеку;
- отдельные методы могут сломаться независимо от версии Tuner;
- использование недокументированных API несёт policy/account risk даже для личного сценария.

## Обязательные меры

- pinned dependency и reproducible image;
- adapter contract tests;
- conservative TTL/call budget/backoff/circuit breaker;
- no polling;
- кешированная read-only деградация;
- write только managed playlists с backup/diff/verify;
- отдельный real smoke перед включением write после upgrade;
- dependency health review минимум раз в квартал или при поломке.

## Условия пересмотра

Пересмотреть решение, если нет compatible release/fix более 90 дней после подтверждённой поломки, OAuth становится неприемлемо рискованным, либо официальный API начинает покрывать нужные YouTube Music операции.

## Источники

- [PyPI](https://pypi.org/project/ytmusicapi/)
- [GitHub](https://github.com/sigma67/ytmusicapi)
- [Latest commits](https://api.github.com/repos/sigma67/ytmusicapi/commits?per_page=5)
- [Stable reference](https://ytmusicapi.readthedocs.io/en/stable/reference/index.html)
- [OAuth setup](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html)
- [Releases](https://github.com/sigma67/ytmusicapi/releases)
