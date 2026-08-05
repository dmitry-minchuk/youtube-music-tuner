# Roadmap разработки

## Принцип поставки

Каждый этап заканчивается работающим вертикальным срезом. Интеграционные записи в YouTube Music добавляются последними, после локального плеера, телеметрии и preview.

## Phase 0 — foundation

- Инициализировать Git и базовые quality tools.
- Создать backend/frontend skeleton и multi-stage Dockerfile.
- Добавить Compose на `127.0.0.1:43127`, named volume `/data` и fixed runtime UID/GID `10001:10001`.
- Реализовать health endpoints, settings и Alembic bootstrap.
- Добавить CI/local команды lint, typecheck, unit test, build.

Definition of done: чистая машина запускает placeholder UI одной командой; restart сохраняет test row; health check работает.

## Phase 1 — read-only YouTube integration

- Реализовать `MusicCatalogPort` и ytmusicapi adapter `1.12.1`.
- CLI OAuth device flow и secure secret files.
- Account, liked tracks, playlists, history sync.
- TTL caches, call ledger, cooldown/circuit breaker.
- UI Settings, Collection и Playlists в read-only режиме.
- Contract fixtures и real read-only smoke.

Definition of done: библиотека видна локально, повторное открытие экранов не вызывает внешний polling, auth error понятен.

## Phase 2 — собственный player и telemetry

- IFrame `PlayerPort` и visible Now Playing layout.
- Queue, play/pause/next/previous/seek/volume.
- IndexedDB event outbox, 15-секундный `progress_tick` и batch API.
- Raw events, session aggregator, reward v1.
- Insights baseline status.
- Fake player tests и browser smoke.

Definition of done: фактические секунды корректно различают listen/seek/buffer; restart/reload не теряет подтверждённые события.

## Phase 3 — Wave v1

- Candidate refresh related/radio/mood с TTL.
- Rule-based cold-start ranker.
- Temperature quotas, mood context, diversity reranker.
- Wave/extend API и главный UI.
- Детерминированные reason codes.

Definition of done: Wave играет минимум 40 элементов из локального pool, температура заметно меняет состав без новых внешних вызовов.

## Phase 4 — online learning

- Feature schema v1 и stored selection snapshots.
- LinUCB model/update/snapshot.
- Bootstrap thresholds, SHADOW с 40-й сессии, чистый rule-based baseline 100 и activation safety gates.
- Offline replay metrics и model rollback.
- Insights сравнение с baseline.

Definition of done: после достаточной реальной телеметрии model snapshot активируется автоматически и воспроизводимо влияет на порядок.

## Phase 5 — безопасная публикация

- Crash-safe создание трёх private managed playlists: CREATING intent, initial adaptive `video_ids`, UNVERIFIED registration, marker reconciliation и verification.
- Versioned playlist quality gates с rule/LinUCB `quality_expected`, adaptive target 25–60, точными pool diagnostics, desired list и minimal convergent diff planner.
- Preview, backup, PARTIAL continuation без нового listening threshold и отдельные caps: 15 item changes, 15 mutating/17 total requests на playlist за 24-часовое окно.
- Fresh read/hash conflict/verification.
- Manual restore и auto-publish cooldown.
- Real write smoke на отдельном test playlist.

Definition of done: плейлисты появляются в официальном iPhone-приложении; никакой чужой playlist не может быть изменён тестами или UI.

## Phase 6 — hardening

- Daily backup + verified retention.
- 24-hour soak и API-budget audit.
- Secret scanning, CSP/Origin/CSRF/Host/DNS-rebinding tests, dependency scan.
- Empty/error/stale/accessibility polish.
- Обновление runbook реальными командами и screenshots.

Definition of done: пройдены AT-01…AT-12, зафиксирована версия образа, auto-publish можно безопасно включить.

## Phase 7 — качество подбора (выполнено 2026-08-01…03)

Всё, что описано ниже, реализовано и проверено на живой библиотеке; подробности — в ADR-004.

- **Замкнут контур обратной связи.** Агрегаты трека и артиста заполняются на приёме телеметрии. До этого они были пусты, и признаки усталости, недавнего пропуска, новизны и rediscovery всегда равнялись нулю.
- **Пул кандидатов стал графом.** Рёбра больше не выбрасываются по TTL, несут расстояние от избранного и наращиваются отдельной работой каждые 6 часов. Кратность связей (сколько избранных треков указывают на кандидата) используется как признак.
- **Ширина прежде глубины.** Нераскрытые избранные раскрываются раньше достижимых кандидатов: обход, жадный по поддержке, углублял один кластер и оставлял большую часть библиотеки непредставленной.
- **Свежесть как ограничение.** Адаптивное окно новизны, память о четырёх последних волнах и об артистах в них, стохастический отбор с окном, растущим вместе с пулом, ограничение ротации знакомого, карантин за повторные пропуски.
- **Публикация перестала быть слепой.** Preview отдаёт состав, играет его и перегенерируется; публикуется просмотренный список; удалить можно любой свой плейлист.
- **Устранены три отказа инфраструктуры.** Tight loop планировщика, потеря телеметрии из-за write-блокировки SQLite и невозможность верификации только что созданного плейлиста.

Измеренный эффект: пересечение соседних волн 95% → 0–25%, повтор discovery-треков → 0%, играбельных кандидатов 119 → 3045, охват избранного 8/51 → 61/61, разных артистов за шесть волн 78 → 161.

## Первый implementation slice

Рекомендуемый первый PR/commit должен содержать только:

1. FastAPI `/health/live` и `/health/ready`;
2. пустую SQLite schema + migration;
3. React shell с пятью разделами и status fetch;
4. Dockerfile/Compose на 43127;
5. unit smoke и container health test.

OAuth и `ytmusicapi` добавляются следующим изолированным slice. Так проблемы Docker/UI не смешиваются с нестабильностью внешней интеграции.

## Отложенные решения

Они не блокируют MVP:

- нужна ли metadata embedding-модель после 500+ сессий;
- добавлять ли LAN access с отдельной auth;
- нужны ли дополнительные managed playlist по mood;
- поддерживать ли PWA/offline shell;
- добавлять ли import из Яндекс Музыки отдельным одноразовым инструментом.

Любое из этих расширений требует отдельного BRD delta и ADR, а не скрытого расширения текущего scope.
