# Внутренний HTTP API

## 1. Общие правила

- Base path: `/api/v1`.
- JSON в `camelCase` наружу, typed Python models внутри.
- Все timestamps — UTC RFC 3339.
- Mutation принимает `Idempotency-Key`, когда операция может быть повторена браузером.
- Ошибка имеет форму `{"error":{"code":"...","message":"...","requestId":"...","retryable":false}}`.
- UI того же origin; CORS выключен по умолчанию.
- Backend отклоняет неизвестный `Host` до routing. Allowlist: hostname из `127.0.0.1`, `localhost`, `[::1]` и port, равный `TUNER_PUBLIC_PORT`; это защита от DNS rebinding, а не функция будущего LAN-режима.
- `GET /api/v1/session` устанавливает случайную HttpOnly `SameSite=Strict` session cookie и возвращает связанный CSRF token. Каждая mutation передаёт token в `X-CSRF-Token`; backend проверяет cookie/token pair и exact `Origin`. Session живёт только локально и инвалидируется после restart.

## 2. System/Auth

| Method | Path | Назначение |
| --- | --- | --- |
| GET | `/health/live` | liveness без обращения к внешним сервисам |
| GET | `/health/ready` | БД и migrations ready |
| GET | `/api/v1/session` | local session cookie + CSRF token |
| GET | `/api/v1/system/status` | версии, jobs, circuit, last sync/train/publish |
| GET | `/api/v1/auth/status` | connected account summary без secrets |
| POST | `/api/v1/auth/disconnect` | отменить jobs и удалить local OAuth после подтверждения |

OAuth bootstrap в MVP выполняется CLI, а UI показывает пошаговую инструкцию и автоматически подхватывает появившийся token file.

## 3. Library и playlists

| Method | Path | Назначение |
| --- | --- | --- |
| GET | `/api/v1/library/tracks?view=liked&cursor=...` | локальный каталог |
| GET | `/api/v1/playlists` | remote + managed summaries |
| GET | `/api/v1/playlists/{id}` | кешированный snapshot |
| POST | `/api/v1/sync` | поставить sync job с cooldown |
| GET | `/api/v1/search?q=...` | кешированный/явный remote search |
| PUT | `/api/v1/tracks/{videoId}/rating` | desired LIKE/DISLIKE/INDIFFERENT |

Pagination cursor opaque; default page 50, maximum 200.

Rating response возвращает `desiredState`, `revision` и `syncStatus`. Повторные PUT для одного `videoId` обновляют одну logical command; job dedupe key не включает state.

## 4. Wave и queue

`POST /api/v1/waves` создаёт snapshot очереди.

Request:

```json
{
  "temperature": 50,
  "mood": "FOCUS",
  "length": 40,
  "excludeVideoIds": ["already-played-id"]
}
```

Response:

```json
{
  "queueId": "uuid",
  "generationId": "uuid",
  "ranking": {
    "phase": "BASELINE",
    "servingPolicy": "rule-score-v1",
    "servingModelId": null,
    "shadowModelId": null,
    "qualityScoreSource": "RULE_MAPPED"
  },
  "mix": {"targetFamiliarPercent": 55, "actualFamiliarPercent": 50, "actualDiscoveryPercent": 50},
  "relaxations": ["FAMILIAR_POOL_WIDENED"],
  "items": [
    {
      "position": 1,
      "track": {"videoId": "...", "title": "...", "artists": ["..."]},
      "reasonCodes": ["RELATED_TO_POSITIVE_SEED", "ARTIST_NOT_RECENT"],
      "familiarity": "DISCOVERY"
    }
  ]
}
```

После bootstrap и до activation SHADOW-ответ явно показывает обучаемую, но не serving-модель:

```json
{
  "ranking": {
    "phase": "SHADOW",
    "servingPolicy": "rule-score-v1",
    "servingModelId": null,
    "shadowModelId": "uuid",
    "qualityScoreSource": "RULE_MAPPED"
  }
}
```

В фазах BASELINE и SHADOW владельцем выдачи всегда остаётся `rule-score-v1`: `servingModelId=null`, а обучаемая модель указывается только как nullable `shadowModelId` и не влияет на items. После activation тот же фрагмент имеет вид:

```json
{
  "ranking": {
    "phase": "ACTIVE",
    "servingPolicy": "linucb-v1",
    "servingModelId": "uuid",
    "shadowModelId": null,
    "qualityScoreSource": "LINUCB_EXPECTED"
  }
}
```

Другие endpoints:

- `POST /api/v1/waves/{queueId}/extend` — ещё 20 локально ранжированных элементов;
- `PATCH /api/v1/waves/{queueId}` — новая температура/mood для непроигранного хвоста;
- `GET /api/v1/waves/{queueId}` — восстановление после reload;
- `POST /api/v1/waves/{queueId}/exclude` — исключить candidate из текущей queue без global dislike.

## 5. Telemetry

`POST /api/v1/telemetry/events:batch` принимает максимум 100 событий или 128 KiB.

```json
{
  "schemaVersion": 1,
  "events": [
    {
      "clientEventId": "uuid",
      "sessionId": "uuid",
      "sequenceNo": 7,
      "videoId": "opaque-id",
      "type": "progress_tick",
      "occurredAt": "2026-08-01T18:42:10.123Z",
      "monotonicMs": 84211,
      "payload": {
        "positionSeconds": 31.2,
        "playedSeconds": 28.7,
        "effectiveDurationSeconds": 213,
        "durationSource": "PLAYER",
        "seekForwardSeconds": 0,
        "seekBackwardSeconds": 0,
        "bufferedSeconds": 2.1,
        "playerState": "PLAYING"
      }
    }
  ]
}
```

Response:

```json
{
  "accepted": 1,
  "duplicates": 0,
  "rejected": [],
  "serverTime": "2026-08-01T18:42:10.190Z"
}
```

Один невалидный event не отклоняет весь batch; rejected содержит event ID и код. Неизвестный `type` для поддерживаемой schema version отклоняется.

## 6. Insights

- `GET /api/v1/insights/summary?period=30d`;
- `GET /api/v1/insights/learning-status`;
- `GET /api/v1/insights/pool`;
- `GET /api/v1/tracks/{videoId}/explanation?generationId=...`;
- `GET /api/v1/diagnostics/api-budget`.

`insights/pool` отдаёт состояние графа кандидатов и свежесть волн: `graphEdges`, `graphCandidates`, `playableCandidates`, `candidatesByHop`, `recentOverlapPercent` (последние 10 генераций), `lastOverlapPercent`, `lastPoolSize`.

Explanation возвращает reason codes и числовые вкладчики, а не сгенерированную LLM историю.

## 7. Managed publishing

| Method | Path | Назначение |
| --- | --- | --- |
| POST | `/api/v1/managed-playlists/setup` | создать либо reconcile три Tuner playlist после preview |
| POST | `/api/v1/managed-playlists/{kind}/plan` | локальный diff без внешней записи |
| POST | `/api/v1/managed-playlists/{kind}/publish` | поставить idempotent job |
| POST | `/api/v1/managed-playlists/{kind}/reconcile` | проверить/adopt CREATING, UNVERIFIED или CLEANUP_REQUIRED exact marker |
| DELETE | `/api/v1/managed-playlists/{kind}/setup-artifact` | после подтверждения удалить только exact remote ID с совпавшим marker |
| GET | `/api/v1/publications/{id}` | state и verification |
| GET | `/api/v1/managed-playlists/{kind}/backups` | доступные snapshots |
| POST | `/api/v1/managed-playlists/{kind}/restore` | спланировать ручное восстановление |

Publish request содержит `expectedRemoteHash` из preview. Если remote playlist изменён после preview, backend отвечает `409 REMOTE_CHANGED` и ничего не записывает.

Initial setup request также содержит accepted desired hash и results `playlist-gates-v2`; backend до create сохраняет CREATING intent, создаёт private playlist сразу с `effectiveTargetSize` video IDs, немедленно регистрирует возвращённый ID как UNVERIFIED и затем верифицирует полный порядок. Setup response всегда возвращает `managedPlaylistId`, `status`, nullable `playlistId`, `configuredTargetSize`, `effectiveTargetSize`, gate reasons и exact pool counts. При уменьшении размера присутствует `TARGET_SIZE_REDUCED_FOR_POOL`; при невозможности набрать минимум 25 backend отвечает без внешней записи:

```json
{
  "status": "SKIPPED_QUALITY",
  "reasonCode": "INSUFFICIENT_POOL",
  "configuredTargetSize": 60,
  "minimumPublishSize": 25,
  "requiredFamiliar": 18,
  "availableFamiliar": 14,
  "requiredDiscovery": 7,
  "availableDiscovery": 19
}
```

Incremental response может иметь `PARTIAL` и возвращает `remainingItemChanges`, `remainingEstimatedRequests`, `nextContinuationAfter` и тот же `desiredHash`. Продолжение выполняется после 24 часов без требования 15 новых sessions, но только после повторных safety/ownership/hash/budget checks.

`DELETE .../setup-artifact` принимает `expectedPlaylistId`, `expectedMarker` и явный `confirm=true` из свежего cleanup preview. Backend вызывает `delete_managed_playlist`, записывает fresh-read/delete в call ledger и возвращает `{"managedPlaylistId":"uuid","status":"DELETED"}` только при подтверждённом success либо remote not-found во время reconciliation. Marker mismatch даёт `403 PLAYLIST_NOT_MANAGED`; неоднозначный внешний ответ сохраняет CLEANUP_REQUIRED и возвращает retryable integration error без автоматического повтора.

## 8. Коды ошибок

| HTTP | Code | Значение |
| ---: | --- | --- |
| 400 | `VALIDATION_FAILED` | неверный DTO |
| 401 | `YTM_AUTH_REQUIRED` | нет/истёк OAuth |
| 403 | `PLAYLIST_NOT_MANAGED` | ownership guard |
| 409 | `PLAYLIST_UNVERIFIED` | обычный publish запрещён до verify/adopt или cleanup |
| 409 | `REMOTE_CHANGED` | optimistic concurrency conflict |
| 409 | `JOB_ALREADY_RUNNING` | dedupe/lease |
| 422 | `PLAYLIST_QUALITY_FAILED` | один или несколько versioned quality gates не пройдены |
| 429 | `LOCAL_BUDGET_EXCEEDED` | внутренний cooldown/budget |
| 502 | `YTM_PARSE_ERROR` | несовместимый внешний payload |
| 503 | `YTM_UNAVAILABLE` | transient external failure |
| 503 | `CIRCUIT_OPEN` | автоматические вызовы приостановлены |

Ни один error response не содержит OAuth endpoint body, headers или stack trace.
