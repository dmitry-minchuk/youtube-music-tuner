# Модель данных

## 1. Хранилище

SQLite в WAL mode — основной persistent store. Все таблицы используют UTC timestamps ISO-8601 или integer epoch с единообразным mapping. Внешние идентификаторы хранятся как opaque strings.

UUID v4 используется для локальных `session_id`, `event_id`, `generation_id`, `job_id`, `instance_id`. Database schema управляется Alembic.

## 2. Основные сущности

### `tracks`

| Поле | Назначение |
| --- | --- |
| `video_id` PK | YouTube video ID |
| `title` | нормализованное название |
| `metadata_duration_seconds` nullable | длительность из library/catalog metadata; player duration хранится в session отдельно |
| `album_id`, `album_title` nullable | альбом |
| `thumbnail_url` nullable | внешний URL обложки |
| `is_playable` | можно ли ставить в очередь |
| `metadata_json` | ограниченный forward-compatible остаток |
| `first_seen_at`, `updated_at`, `remote_deleted_at` | lifecycle |

Артисты нормализуются в `artists` и `track_artists(track_id, artist_id, ordinal)`. Отсутствующий album не является ошибкой.

### `library_track_state`

Одна строка на track:

- `is_liked`, `is_disliked`, `is_in_library`;
- `remote_rating_synced_at`;
- `desired_rating`, `rating_revision`, `rating_synced_revision`, `rating_sync_status`;
- `source_snapshot_id`, `updated_at`.

### `taste_vetoes`

Локальный сильный негатив «Don't Like At All» (docs/05 §11). Отдельная таблица, потому что affinity-агрегаты идемпотентно пересобираются из сессий и не могут нести ручной сигнал:

- `video_id` — PK, FK на `tracks`;
- `artist_id` — основной артист на момент veto (snapshot-fallback; на волне артист разрешается заново из `track_artists`);
- `source` — `MANUAL` (кнопка), `FARM_AUTO` (фермо-детектор), `OVERRIDDEN` (снятое пользователем авто-veto; хранится инертным, чтобы детектор не переспорил человека);
- `created_at`.

Никогда не синхронизируется в YouTube.

### `remote_playlists` и `remote_playlist_items`

Snapshot удалённых плейлистов, включая порядок. Для каждого snapshot фиксируются `fetched_at`, `content_hash` и `track_count`.

### `managed_playlists`

- `managed_playlist_id` UUID PK;
- `playlist_id` nullable UNIQUE — remote ID появляется только после ответа create;
- `kind`: FAMILIAR | BALANCE | DISCOVERY;
- `instance_id`;
- `ownership_marker`;
- `temperature`;
- `configured_target_size` default 60;
- `status`: CREATING | UNVERIFIED | ACTIVE | CLEANUP_REQUIRED | DELETED;
- `accepted_desired_hash` и setup timestamps/error code для crash-safe reconciliation;
- `last_published_at`, `next_publish_after`;
- `auto_publish_enabled`.

Setup intent создаётся до внешнего create. Playlist ID сохраняется и статус меняется на UNVERIFIED сразу после возврата ID, до verification read. Обычная запись разрешена только для ACTIVE при совпадении playlist ID, local instance и remote marker. Для UNVERIFIED/CLEANUP_REQUIRED разрешены только verify/adopt либо явно подтверждённый cleanup exact playlist с совпавшим marker; restart сначала reconciles эти manifests и не создаёт дубликат. Переход `UNVERIFIED|CLEANUP_REQUIRED → DELETED` выполняется только после успешного `delete_playlist` либо fresh reconciliation, подтвердившего remote not-found; tombstone сохраняет instance/playlist ID, marker, deletion timestamp и outcome ledger ID.

## 3. Телеметрия

### `telemetry_events`

Append-only таблица:

- `client_event_id` UNIQUE;
- `session_id`, `sequence_no` с UNIQUE pair;
- `event_type`, `video_id`;
- `occurred_at_client`, `received_at_server`;
- `monotonic_ms`;
- `schema_version`;
- `payload_json` после server-side validation.

Индекс: `(session_id, sequence_no)`, `(video_id, received_at_server)`, `(event_type, received_at_server)`.

### `playback_sessions`

- `session_id` PK, `video_id`, `queue_id`, `generation_id`;
- `started_at`, `ended_at`, `termination_reason`;
- `effective_duration_seconds` nullable, `duration_source`: PLAYER | METADATA | UNKNOWN;
- `played_seconds`, `played_ratio` nullable, `max_position_seconds`;
- `seek_forward_seconds`, `seek_backward_seconds`, `seek_forward_count`, `seek_backward_count`, `buffered_seconds`;
- `explicit_rating`, `early_skip`, `mid_skip`, `completed`, `ended_unqualified`, `large_forward_seek`, `replayed`;
- `classification_basis`: RATIO | ABSOLUTE_TIME;
- `qualified`, `reward`, `reward_version`;
- `source`: TUNER | REMOTE_HISTORY;
- `aggregation_version`, `aggregated_at`.

Raw events остаются источником повторного расчёта только пока находятся внутри retention. После их удаления session summary с исходными `reward_version` и `aggregation_version` является неизменяемой исторической записью.

### `remote_history_items`

Хранит только доступный факт истории и timestamp/order. Строка не имитирует playback session и никогда не имеет `played_seconds` или `skip`.

## 4. Candidate graph и признаки

### `candidate_edges`

- `seed_video_id`, `candidate_video_id`;
- `source_type`: RELATED | RADIO | MOOD | PLAYLIST;
- `source_key`, `rank`, `fetched_at`, `expires_at`;
- `hop` — расстояние от positive root, 1..3;
- UNIQUE `(seed_video_id, candidate_video_id, source_type, source_key)`;
- индексы по `seed_video_id` и `candidate_video_id` для обхода графа.

`expires_at` — не срок жизни строки, а отметка «seed можно опросить заново». Ребро остаётся в графе и после этой даты: оно фиксирует факт о каталоге, а не кэширует ответ. Ранжирование читает весь граф, а не свежее окно.

### `remote_playlists`

`track_count` — nullable. YouTube не возвращает размер для своих системных плейлистов (`LM` — Liked Music, `SE` — Episodes for Later), и запись нуля выдавала «неизвестно» за «пусто». Для Liked Music API отдаёт локально известное число лайков: это та же сущность, и её мы знаем точно.

### `track_affinity` и `artist_affinity`

Материализованные агрегаты по track/artist:

- play counts 1/7/30/all time;
- completion/skip/replay counts;
- decayed reward — экспоненциально взвешенное среднее с полураспадом 30 дней;
- last played/liked/skipped;
- confidence and aggregate version.

Таблица ускоряет ранжирование, но может быть полностью пересоздана из sessions/library.

Обновление происходит **сразу после переагрегации сессии**, а не суточным батчем: пропуск должен влиять на следующий трек, а не на завтрашнюю выдачу. Пересчёт всегда полный по треку (а не инкрементальный), потому что батч телеметрии может прийти повторно — так счётчики не раздуваются. Job `affinity_rollup` раз в 6 часов пересчитывает всё: скользящие окна 1/7/30 дней сужаются со временем и без новых прослушиваний.

Пока эти таблицы были пусты, `fatigue`, `recent_skip`, `novelty` и `rediscovery` тождественно равнялись нулю, то есть история прослушиваний не влияла на ранжирование вообще.

### `feature_snapshots`

Хранит feature vector для каждого реально выбранного кандидата, serving policy/model ID и feature schema момента выбора. Это обязательный источник корректного online update: модель обучается на признаках момента выбора, а не на пересчитанных будущих данных.

## 5. Модель и генерации

### `model_snapshots`

- `model_id` UUID PK;
- `algorithm` = `linucb-v1`;
- `feature_schema_version`;
- `parameters_blob` — компактный NumPy-compatible binary или JSON для малой матрицы;
- `trained_through_session_id/time`;
- `training_counts_json`, `metrics_json`;
- `status`: CANDIDATE | SHADOW | ACTIVE | REJECTED | RETIRED;
- `created_at`, `activated_at`.

До activation ACTIVE snapshot может отсутствовать; после activation одновременно активен не более чем один snapshot на algorithm/schema. SHADOW snapshots могут существовать параллельно и не используются serving ranker.

### `queue_generations` и `queue_items`

Generation хранит temperature, mood, serving policy, nullable serving/shadow model IDs, `quality_score_source`, configured/effective target size, random seed, candidate pool watermark и полный упорядоченный результат с score/reason codes. Это обеспечивает воспроизводимость и объяснения как для rule baseline, так и для ACTIVE LinUCB.

Дополнительно фиксируются два показателя свежести: `pool_size` — сколько играбельных кандидатов было доступно, и `overlap_previous_percent` — какую долю этой волны уже содержала предыдущая. Последние генерации сами являются входом следующей (docs/05 §11), поэтому воспроизводимость по `random_seed` означает «то же состояние базы плюс тот же seed», а не «повторный вызов подряд».

### `playlist_publications`

Состояния:

```text
PLANNED → WRITING → VERIFYING → COMPLETE
                    │
                    ├────────→ PARTIAL → PLANNED
                    └────────→ FAILED
```

Запись содержит remote-before snapshot, immutable desired snapshot/hash, target generation, `quality_gate_version` и результаты gates, configured/effective target size, ограниченный diff, remaining item/request counts, item-change/request budgets, expected intermediate hash, applied operations, error code и verification hash. PARTIAL продолжает тот же desired hash, пока не достигнет COMPLETE или не будет явно инвалидирован safety-событием. Продолжение не ждёт новых 15 sessions, но имеет отдельное `not_before` следующего 24-часового окна и снова проходит ownership/hash/safety/budget checks.

## 6. Jobs и внешние вызовы

### `jobs`

- `job_id`, `job_type`, `dedupe_key` UNIQUE для активного job;
- `status`: PENDING | RUNNING | SUCCEEDED | FAILED | CANCELLED;
- `not_before`, `attempt`, `max_attempts`;
- `locked_by`, `locked_until`;
- `payload_json`, `result_json`, `last_error_code`;
- timestamps.

Для rating используется `dedupe_key=rating:{video_id}` без desired state в ключе. Payload содержит latest desired state и revision; завершение job подтверждает только ту revision, которую реально отправило.

### `api_call_ledger`

- provider/operation;
- playlist/publication ID nullable и `request_kind`: READ | MUTATION;
- request fingerprint без секретов;
- job/request ID;
- started/finished/duration;
- outcome/status class;
- retry-after/circuit state;
- response size, но не полный response.

Ledger используется для бюджета и диагностики, не как analytics export.

## 7. Settings и secrets

`settings` содержит только несекретные values: default temperature, mood, automation flags, retention days, publish window. Secret files находятся отдельно в `/data/secrets` и никогда не записываются в SQLite.

`schema_metadata` хранит DB version, app instance UUID и время создания. Account ID, если нужен для защиты от случайного подключения другого аккаунта, хранится в хешированном/минимальном виде и показывается UI из свежего account DTO.

## 8. Retention

По умолчанию:

- raw telemetry events — 180 дней;
- playback session summaries — бессрочно до ручного удаления;
- api call ledger — 90 дней;
- failed job detail — 30 дней; успешные jobs — 7 дней;
- candidate edges — удаляются, если не обновлялись год; `expires_at` их не удаляет (§4);
- feature snapshots — 180 дней, синхронно с raw telemetry, после чего online update опирается на сохранённые session/model aggregates;
- playlist backups — последние 30 snapshots на managed playlist;
- model snapshots — активный + последние 10;
- daily database backups — 14 дней.

Retention cleanup удаляет только после успешного backup и никогда не удаляет session aggregates вместе с raw events.

Следствие retention: смена reward/aggregation formula пересчитывает retained 180-дневное окно и новые события. Более старые summaries не обещают повторный расчёт и не переписываются; baseline snapshot также остаётся замороженным на исходной версии формулы. Пользователь может увеличить retention до начала сбора данных, если хочет более длинный горизонт воспроизводимого backfill.

## 9. Удаление данных

`Delete telemetry` выполняется транзакционно:

1. создать optional user-requested backup только при явном выборе;
2. удалить raw events, playback sessions, affinity, feature/model/queue snapshots;
3. сохранить библиотечный cache и managed playlist manifest, если пользователь не выбрал full reset;
4. записать локальный audit факт удаления без музыкальных данных;
5. пересоздать пустую модель.

`Full reset` также удаляет OAuth-файлы и БД после остановки процесса. Операция должна быть описана точными путями и требовать подтверждения.
