# Комплект проектной документации

Документы описывают целевое состояние первого рабочего релиза YouTube Music Tuner. Формулировка «должен» означает требование к реализации; фактический статус разработки отражается только в корневом `README.md` и roadmap.

## Revision 1.2.1 — 2026-08-01

Cleanup неверифицированного setup artifact доведён до интеграционного контракта: добавлены `delete_managed_playlist`/`delete_playlist`, точный scope, отдельный двухзапросный budget, переход в DELETED и contract fixtures. Rule-score mapping явно обозначен как некалиброванное монотонное преобразование; первый real preview обязан измерить pass rate порога 0.40.

## Revision 1.2 — 2026-08-01

Повторный review закрыл раннюю публикацию без ACTIVE-модели через общий `quality_expected`, адаптивный размер 25–60 и точные pool diagnostics, crash-safe CREATING/UNVERIFIED lifecycle, продолжение PARTIAL без повторного listening threshold, независимые item/request budgets и однозначный BASELINE/SHADOW/ACTIVE API. Также уточнены квалификация короткого explicit Next, reward precedence, proportional artist diversity и retention feature snapshots.

## Revision 1.1 — 2026-08-01

После сквозного архитектурного review уточнены clean baseline/SHADOW activation, reward normalization, `progress_tick`, unknown-duration/seek classification, revision-aware rating jobs, initial playlist fill, versioned playlist quality gates, convergent PARTIAL ordering, single-owner diversity rules, named-volume UID/GID, CSRF и DNS-rebinding protection. Policy-пауза при hidden сохранена на основании актуальных YouTube Developer Policies.

## Порядок чтения

1. [Продуктовые требования](01-product-requirements.md) — зачем существует продукт и что входит в MVP.
2. [Системная архитектура](02-system-architecture.md) — компоненты, границы и поток данных.
3. [Интеграция с YouTube Music](03-youtube-integration.md) — `ytmusicapi`, OAuth, синхронизация и лимиты.
4. [Плеер и телеметрия](04-player-and-telemetry.md) — как измеряется реальное слушание.
5. [Рекомендательный движок](05-recommendation-engine.md) — candidate generation, обучение и температура.
6. [UI/UX](06-ui-ux.md) — информационная архитектура и визуальные принципы.
7. [Модель данных](07-data-model.md) — таблицы, идентификаторы и хранение.
8. [Внутренний API](08-api-contract.md) — контракт frontend/backend.
9. [Docker и эксплуатация](09-operations-docker.md) — запуск, health check, backup и восстановление.
10. [Безопасность, приватность и риски](10-security-privacy-risks.md).
11. [Тестирование и приёмка](11-testing-acceptance.md).
12. [Roadmap](12-roadmap.md).
13. [Источники](13-references.md) — изученная документация и дата проверки.

## Architecture Decision Records

- [ADR-001: использовать ytmusicapi](decisions/ADR-001-use-ytmusicapi.md);
- [ADR-002: локальный рекомендатель без обязательной LLM](decisions/ADR-002-local-recommender-no-llm.md);
- [ADR-003: один контейнер и порт 43127](decisions/ADR-003-single-container-port.md).

## Правила актуализации

- При изменении продуктового поведения сначала обновляется соответствующее требование и критерий приёмки.
- При смене фундаментального технического решения добавляется новый ADR, который заменяет старый; старый ADR не удаляется.
- Версия `ytmusicapi`, OAuth-процесс и поддерживаемые методы перепроверяются перед каждым обновлением зависимости.
- Документ не должен содержать OAuth-токены, Google client secret, cookies или реальные идентификаторы приватных плейлистов.
