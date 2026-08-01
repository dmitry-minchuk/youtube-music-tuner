# Безопасность, приватность и риски

## 1. Модель использования

Приложение предназначено для одного владельца, запускается на доверенном домашнем компьютере и доступно только через loopback. Это уменьшает поверхность атаки, но не отменяет защиту OAuth-токена и проверку всех входных данных.

Приложение не предназначено для публичного хостинга, продажи, передачи другим пользователям или обхода ограничений YouTube.

## 2. Чувствительные данные

| Данные | Место | Защита |
| --- | --- | --- |
| OAuth refresh/access token | `/data/secrets/oauth.json` | `0600`, не в Git/логах/backup без явной защиты |
| Google client secret | `/data/secrets/client.json` | `0600`, импорт CLI внутрь named volume, не env |
| История/телеметрия | SQLite | loopback-only app, local retention/delete |
| Playlist IDs/video IDs | SQLite | считаются персональными metadata, не публикуются |
| Database backups | `/data/backups` | локальные права, checksum, retention |

В MVP шифрование БД приложением не добавляется: оно создаст собственное управление ключами. Для защиты диска предполагается включённый FileVault на macOS. Если `/data` переносится на NAS или общий диск, модель безопасности должна быть пересмотрена.

## 3. Web security

- host publish только `127.0.0.1`;
- CORS выключен;
- middleware до routing принимает только `Host` из `127.0.0.1`, `localhost`, `[::1]` с configured port, блокируя DNS rebinding уже в MVP;
- mutation проверяет exact `Origin`, HttpOnly `SameSite=Strict` local session cookie и связанный `X-CSRF-Token`;
- CSP разрешает собственные resources и необходимые `script-src`/`frame-src` домены YouTube IFrame, без `unsafe-eval`;
- iframe получает минимальные необходимые permissions;
- API не принимает произвольный URL для fetch, только opaque IDs;
- response headers: `X-Content-Type-Options`, `Referrer-Policy: strict-origin-when-cross-origin`, `frame-ancestors 'self'` для UI; политика referrer не должна скрывать origin от YouTube player;
- diagnostics не возвращает filesystem secrets или stack traces.

Если позже нужен LAN-доступ, обязательны HTTPS reverse proxy, аутентификация приложения, trusted-host allowlist и новый threat review. Простая смена bind на `0.0.0.0` запрещена.

## 4. Secret handling

- `.gitignore` исключает `data/`, `secrets/`, OAuth/cookie JSON и `.env`.
- Лог-filter редактирует ключи `authorization`, `cookie`, `client_secret`, `access_token`, `refresh_token`, `visitorData` независимо от регистра.
- Исключения интеграции логируются по безопасному code и request ID, без полного request/response.
- OAuth-файлы читаются при старте/refresh и не передаются frontend.
- Backup OAuth создаётся только при явной настройке; обычный backup может исключать `/data/secrets`.
- Disconnect удаляет token file, но UI также даёт ссылку/инструкцию для отзыва доступа в Google Account.

## 5. Защита удалённых данных

Наибольший прикладной риск — испортить существующий плейлист. Предохранители:

- обычный publish только для ACTIVE ID из `managed_playlists`;
- create intent сохраняется до внешнего вызова, возвращённый ID — как UNVERIFIED до verification; restart reconciles marker вместо создания дубликата;
- совпадение remote ownership marker и local instance UUID; UNVERIFIED можно только verify/adopt или явно удалить по exact ID+marker;
- private visibility при создании;
- playlist quality gates до любого create/publish;
- fresh read и optimistic `content_hash` перед записью;
- полный before-snapshot;
- preview diff;
- initial create одним подтверждённым вызовом с `effective_target_size` IDs и немедленной полной verification; затем максимум 15 item changes и 15 mutating requests за окно;
- per-playlist endpoint cap 17 (fresh read + до 15 mutations + verify) и global automatic cap 51 requests/сутки для трёх playlists;
- PARTIAL publication продолжает immutable desired hash без нового listening threshold, но ждёт 24 часа и заново проходит safety/ownership/hash/budget checks;
- state machine и verification read;
- ручное восстановление, без автоматического слепого rollback;
- автопубликация выключена до успешного тестового цикла.

## 6. Неофициальный API

`ytmusicapi` прямо позиционируется как unofficial API и повторяет запросы клиента YouTube Music. Это означает:

- внутренние endpoints и payload могут измениться без notice;
- отдельные методы могут временно перестать работать;
- OAuth/cookie поведение может измениться;
- официальная поддержка Google отсутствует;
- чрезмерные/нехарактерные запросы могут привести к throttling или другим ограничениям аккаунта.

Для личного проекта риск принят. Mitigation: pinned version, adapter boundary, низкий call budget, exponential backoff, circuit breaker, fixtures, real read-only smoke и отсутствие bulk операций.

## 7. Правила YouTube и playback

Официальные YouTube API policies требуют использовать документированные API и накладывают требования на embedded player. Одновременно `ytmusicapi` основан на недокументированном интерфейсе. Некоммерческий/личный характер уменьшает масштаб последствий, но не превращает интеграцию в официально поддержанную.

Поэтому архитектура:

- явно документирует экспериментальный характер;
- для playback использует официальный IFrame API;
- держит player видимым и достаточного размера;
- ставит playback на паузу при `document.visibilityState=hidden`, потому что политика относит player вне просматриваемой вкладки/экрана к background player;
- не загружает/не хранит media;
- не скрывает обязательные controls/branding;
- не строит отдельные derived YouTube aggregate metrics для передачи третьим сторонам;
- не предоставляет сервис другим пользователям.

Перед любым публичным размещением или коммерциализацией разработку нужно остановить и заново пройти policy/legal review; текущая архитектура на это не рассчитана.

## 8. Privacy

- Raw telemetry не покидает компьютер.
- Нет Sentry, Google Analytics, внешней LLM или cloud vector DB.
- UI показывает, какие события собираются и как они интерпретируются.
- Context по времени суток выключаемый; микрофон, камера, геолокация и контакты не используются.
- Пользователь может удалить raw telemetry отдельно либо выполнить full reset.
- Экспорт по умолчанию содержит агрегаты без OAuth и внешних private IDs; полный debug export требует дополнительного подтверждения.

## 9. Risk register

| Риск | Вероятность | Влияние | Меры | Остаточный риск |
| --- | --- | --- | --- | --- |
| Изменение внутренних YTM payload | высокая | высокий | adapter, pin, tests, cached fallback | средний |
| Отзыв/истечение OAuth | средняя | средний | clear reconnect, stop writes | низкий |
| Повреждение managed playlist | низкая | высокий | marker, hash, backup, diff, verify | низкий-средний |
| API spam/throttling | низкая | высокий | ledger, TTL, daily budgets, circuit | низкий |
| Утечка токена через Git/log | низкая | высокий | ignore, file secret, redaction tests | низкий |
| Неверная интерпретация skip | средняя | средний | explicit next only, neutral unknown, raw events | низкий-средний |
| Модель зациклилась на артистах | средняя | средний | diversity gates, monitoring, rollback model | низкий |
| IFrame трек недоступен/реклама | средняя | низкий-средний | neutral error, skip candidate; не обходить ограничения | средний |
| Public/LAN exposure по ошибке | низкая | высокий | hardcoded loopback Compose mapping, startup warning | низкий |
| Проект перестал работать после update | средняя | средний | backups, pinned images/deps, upgrade runbook | низкий-средний |

## 10. Incident actions

### Подозрение на утечку OAuth

1. Остановить контейнер.
2. Отозвать доступ приложения в Google Account.
3. Удалить `/data/secrets/oauth.json`.
4. Проверить Git history и логи на secret pattern.
5. При утечке client secret удалить OAuth client и создать новый.
6. Возобновить работу только после проверки.

### Неожиданное изменение плейлиста

1. Отключить auto-publish/circuit.
2. Сохранить текущее remote состояние и call ledger.
3. Сравнить publication plan/applied operations/verification.
4. Восстановить выбранный snapshot вручную после preview.
5. Не повторять publish до исправления причины и test-playlist smoke.
