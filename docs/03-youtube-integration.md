# Интеграция с YouTube Music

## 1. Решение

Для чтения библиотеки, генерации кандидатов и изменения плейлистов используется `ytmusicapi`, начиная с точно закреплённой версии `1.12.1`. Воспроизведение выполняется не этой библиотекой, а YouTube IFrame Player API в браузере.

Причины и проверка жизнеспособности библиотеки зафиксированы в [ADR-001](decisions/ADR-001-use-ytmusicapi.md). Риск неофициального API принят только для личного локального инструмента.

## 2. Аутентификация

### Проверенный факт: собственный OAuth client не работает

Проверка 2026-08-01 на реальном аккаунте: device flow с собственным Google Cloud client типа `TVs and Limited Input devices` проходит успешно, Google возвращает валидный refresh token со scope `https://www.googleapis.com/auth/youtube`, но **любой** последующий вызов internal-API YouTube Music отвечает `HTTP 400 Bad Request: Request contains an invalid argument` — включая `get_account_info`, `get_liked_songs`, `get_library_playlists`, `get_history` и `search`. Неавторизованный `search` через тот же контейнер при этом работает, то есть транспорт и парсеры исправны.

Вывод: YouTube Music принимает Bearer-токены только от собственных клиентов Google, а не от клиента, созданного пользователем. Токен формально валиден, но для internal API бесполезен.

### Основной вариант — browser authentication

Три равнозначных пути, все сохраняют один и тот же файл `/data/secrets/browser.json` с правами `0600`:

1. **Кнопка Connect в Settings.** Пользователь копирует request headers из DevTools и вставляет их в поле. `POST /api/v1/auth/browser-headers` проходит те же guard-ы, что и любая mutation: exact Origin, HttpOnly SameSite=Strict cookie, CSRF-токен, Host allowlist. Значение не возвращается в ответе и не логируется.
2. **`./connect.sh`.** Открывает видимое окно браузера, ждёт логина, перехватывает заголовки настоящего `/youtubei/` запроса, импортирует их и затирает промежуточный файл.
3. **`python -m app.cli browser import -`.** Ручной путь для случая, когда UI недоступен.

Любой путь дополняет вставку тем, что обычно теряется при копировании: `authorization` выводится из cookie (`__Secure-3PAPISID`), `x-goog-authuser` по умолчанию `0`. Успех подтверждается только после реального `get_liked_songs(limit=1)`; если YouTube Music отвергает заголовки, файл удаляется, а не остаётся сломанным.

Проверка состояния: `python -m app.cli status` или Settings — ожидается `method=BROWSER`.

Cookies живут долго, но не вечно: при `AuthError` UI показывает reconnect, и headers копируются заново. Это цена работы поверх неофициального интерфейса.

### Резервный вариант — OAuth device flow

Оставлен в коде на случай, если Google снова начнёт принимать пользовательские клиенты: `credentials import` → `auth` → `/data/secrets/oauth.json`. Адаптер использует OAuth только если `browser.json` отсутствует.

### Изменение границы: UI принимает cookies

Исходное правило «никакой credential не проходит через web UI» было написано в расчёте на рабочий OAuth. После того как OAuth оказался неприменим, единственным способом подключиться остался ручной ввод заголовков в терминал, что для локального личного инструмента непропорционально неудобно. Решение пересмотрено: **cookies принимаются формой в Settings**, потому что риск не меняется качественно — интерфейс доступен только на loopback, CORS выключен, действуют Host allowlist, exact Origin, SameSite=Strict cookie и CSRF-токен, CSP запрещает сторонние скрипты, а сами cookies и так находятся в браузере пользователя.

Что сохраняется без изменений: значение никогда не возвращается в ответе, не попадает в логи (log-filter редактирует `cookie` и `authorization`), хранится только файлом `0600` внутри контейнера и удаляется по `disconnect`.

**Client secret по-прежнему не принимается через UI** — только `python -m app.cli credentials import`.

## 3. Adapter boundary

Домен вызывает интерфейс, не зависящий от формата `ytmusicapi`:

```python
class MusicCatalogPort(Protocol):
    def account(self) -> Account: ...
    def liked_tracks(self, limit: int | None = None) -> list[Track]: ...
    def library_playlists(self) -> list[RemotePlaylist]: ...
    def playlist(self, playlist_id: str) -> RemotePlaylistSnapshot: ...
    def history(self) -> list[RemoteHistoryItem]: ...
    def related(self, video_id: str) -> list[TrackCandidate]: ...
    def radio(self, video_id: str, limit: int) -> list[TrackCandidate]: ...
    def rate_track(self, video_id: str, rating: Rating) -> None: ...
    def create_private_playlist(self, title: str, description: str, video_ids: list[str]) -> str: ...
    def apply_playlist_diff(self, plan: PlaylistDiffPlan) -> None: ...
    def delete_managed_playlist(self, playlist_id: str, expected_marker: str) -> None: ...
```

Adapter обязан:

- преобразовывать внешние dict payload в внутренние typed-модели;
- валидировать обязательные `videoId`, title и artist list;
- сохранять `None`, а не выдумывать отсутствующий album/duration;
- классифицировать ошибки как `AuthError`, `RateLimited`, `RemoteChanged`, `ParseError`, `Unavailable`;
- не пропускать raw exception и внешние структуры в domain/UI.

## 4. Используемые методы ytmusicapi

| Задача | Метод | Частота/кеш |
| --- | --- | --- |
| Проверка аккаунта | `get_account_info()` | при старте и reconnect |
| Лайки | `get_liked_songs()` | не чаще раза в 6 часов |
| Плейлисты | `get_library_playlists()` | не чаще раза в 6 часов |
| Состав плейлиста | `get_playlist()` | TTL 6 часов; перед publish обязательно свежий read |
| Доступная история | `get_history()` | не чаще раза в 6 часов; только вспомогательный сигнал |
| Поиск | `search()` | только по явному действию, debounce 400 мс, TTL 24 часа |
| Похожие треки | `get_song_related()` | seed не переспрашивается 7 дней; полученные рёбра остаются в графе |
| Радио/очередь | `get_watch_playlist(..., radio=True)` | seed не переспрашивается 7 дней; полученные рёбра остаются в графе |
| Mood sources | `get_mood_categories()`, `get_mood_playlists()` | TTL 30 дней/7 дней |
| Like/dislike | `rate_song()` | только изменение состояния, debounce/idempotency |
| Создание | `create_playlist(..., video_ids=desired)` | только подтверждённый initial managed setup |
| Изменение | `add_playlist_items()`, `remove_playlist_items()`, `edit_playlist()` | publish job |
| Удаление | `delete_playlist(playlistId)` | только явно подтверждённое удаление собственного managed playlist после fresh marker check |

Загрузка музыки, удаление чужих плейлистов и обычных пользовательских сущностей находятся вне scope. Единственное исключение — удаление собственного managed playlist (UNVERIFIED, CLEANUP_REQUIRED либо ACTIVE) с exact ID, fresh ownership marker и отдельным подтверждением: это плейлисты слушателя, и защищает их не статус, а маркер.

## 5. Синхронизация библиотеки

Синхронизация — snapshot-oriented:

1. Проверить cooldown и OAuth.
2. Создать `sync_run` со статусом RUNNING.
3. Получить лайки и список плейлистов.
4. Нормализовать треки по `videoId`; записи без `videoId` сохранить как metadata-only и не ставить в очередь.
5. Upsert удалённые сущности, не удаляя локальную телеметрию.
6. Для исчезнувших объектов выставить `remote_deleted_at`, а не физически удалять.
7. Сохранить watermark и counts.
8. Завершить SUCCESS либо FAILED с безопасным кратким кодом ошибки.

История YouTube Music не используется для вывода «пользователь пропустил трек»: у неё нет достоверной длительности прослушивания. Она лишь повышает recency/fatigue и подтверждает факт недавнего запуска композиции вне Tuner.

## 6. Обновление like/dislike

UI сначала фиксирует явное событие локально. Для одного трека существует одна mutable-команда с dedupe key `rating:{video_id}`; `desired_state` и монотонно растущая `revision` находятся в payload. Каждое быстрое переключение транзакционно обновляет payload существующей PENDING-команды и переносит `not_before` на две секунды после последнего действия.

Worker перед вызовом читает самую новую revision. После внешнего ответа он помечает rating синхронизированным, только если revision всё ещё совпадает. Если пользователь успел изменить состояние во время RUNNING, завершившаяся операция не подтверждает новое состояние: после освобождения dedupe key создаётся/возобновляется successor job с последним payload. Так последовательность like → dislike никогда не оставляет устаревший like финальным состоянием.

Ошибка внешней записи не откатывает локальное событие поведения, но UI показывает `not synced`. Retry снова читает актуальный desired state, а не payload первоначальной попытки.

## 7. Генерация кандидатов без API-спама

Candidate refresh выполняется отдельно от ранжирования:

- выбрать максимум 6 seed за один run из positive roots — лайков **и** треков с сильной локальной наградой; ещё не раскрытые избранные идут первыми, потому что нераскрытый лайк не даёт пулу ничего;
- для каждого seed сделать максимум один `related` и один `radio` вызов, только если seed давно не опрашивался;
- сохранить edge `seed → candidate`, источник, позицию, расстояние от корня (`hop`) и время получения;
- дедуплицировать по `videoId`, но **не схлопывать кратность**: сколько разных избранных треков указывают на кандидата — самый сильный доступный сигнал;
- не обновлять candidate pool чаще одного раза в сутки автоматически; расширение границы графа идёт отдельной работой каждые 6 часов;
- при открытии Wave работать только с локальным pool;
- при пустом pool разрешить одну foreground refresh-операцию с явным статусом UI.

## 8. Управляемые плейлисты

Tuner поддерживает ровно три стабильных плейлиста по умолчанию:

- `Tuner · Familiar` — температура 20;
- `Tuner · Balance` — температура 50;
- `Tuner · Discovery` — температура 80.

При первом создании в description записывается marker вида `Managed by YouTube Music Tuner; instance=<uuid>; schema=1`. Локальная таблица хранит nullable playlist ID и тот же instance UUID. Для обычного publish нужны статус ACTIVE и совпадение ID/marker; для UNVERIFIED разрешены только verify/adopt либо явно подтверждённый cleanup.

### Первичное создание

После явного preview/подтверждения каждый desired list сначала проходит `playlist-gates-v2`, включая выбор `effective_target_size` от 25 до configured default 60. До внешнего вызова Tuner транзакционно создаёт локальный setup intent со статусом CREATING, instance UUID, ownership marker и принятым desired hash. Затем вызывается один `create_playlist(title, description, privacy_status="PRIVATE", video_ids=desired)` со всеми `effective_target_size` треками в целевом порядке. Это initial-create операция, а не incremental diff, поэтому лимит 15 item changes на неё не распространяется.

Сразу после возврата playlist ID он записывается в тот же manifest, статус меняется на UNVERIFIED **до** verification read, и запись **коммитится**: одного flush недостаточно, потому что падение процесса откатило бы её, а открытая транзакция вдобавок держит единственный write-lock SQLite на всё время внешних вызовов.

Затем Tuner вызывает `get_playlist(limit=None)` и проверяет marker, effective size, уникальность и полный порядок. Чтение выполняется до трёх раз (сразу, через 2 и через 5 секунд): только что созданный playlist не читается мгновенно — YouTube отвечает неполным payload, который не парсится. Одной попытки не хватало, и все три playlist оставались UNVERIFIED, хотя были созданы корректно.

Порядок сверяется с **хешем согласованного списка**, записанным в manifest, а не с заново сгенерированным списком: очередь каждый раз другая, поэтому повторный `Verify/adopt` иначе всегда возвращал бы `VERIFICATION_MISMATCH`.

При успехе manifest становится ACTIVE. При несовпадении или ошибке чтения он остаётся UNVERIFIED либо получает CLEANUP_REQUIRED; Tuner не пытается автоматически «долить», переставить или создать замену.

После crash/restart setup прежде всего возобновляет CREATING/UNVERIFIED manifests, а не создаёт новый playlist. Если create мог пройти, но ответ не был сохранён, reconciliation ищет среди свежих remote playlists только точный instance marker и expected title, затем проверяет desired hash. Один точный match принимается как UNVERIFIED и проходит verify; ноль остаётся CREATING для безопасного ручного retry, несколько переводят setup в CLEANUP_REQUIRED. UI позволяет отдельно `Verify/adopt` или удалить только exact ID с совпадающим marker после явного подтверждения. Произвольный playlist и manifest без совпавшего marker удалить этим путём нельзя.

Cleanup вызывает `delete_managed_playlist(playlist_id, expected_marker)`. Реализация порта сначала делает fresh `get_playlist(limit=None)` и сравнивает exact marker, затем выполняет ровно один `delete_playlist(playlistId)`. Подтверждённый success переводит manifest в DELETED. Если delete response потерян/неоднозначен, manifest остаётся CLEANUP_REQUIRED; слепого retry нет. Следующий явно подтверждённый reconciliation начинает с нового read: remote not-found переводит manifest в DELETED, найденный exact marker разрешает новую одиночную попытку, mismatch запрещает удаление.

### Последующие публикации

Алгоритм publish:

1. Захватить lease и проверить дневной cooldown.
2. Если существует PARTIAL publication, продолжить её зафиксированный desired snapshot; более новая generation ожидает и не меняет движущуюся цель. Это продолжение не требует 15 новых сессий или нового rating, но выполняется только в следующем 24-часовом окне. Новый explicit dislike/block или недоступность target item инвалидирует plan до следующей записи.
3. Получить свежий remote snapshot и убедиться, что playlist зарегистрирован, marker совпадает, а remote hash соответствует последнему проверенному промежуточному состоянию.
4. Сохранить полный backup текущего порядка.
5. Только для нового цикла проверить trigger из 15 новых квалифицированных сессий/изменённого rating, построить desired list с адаптивным `effective_target_size` и проверить все playlist quality gates. Для PARTIAL использовать сохранённый desired и повторно проверить hard/safety gates.
6. Рассчитать детерминированный diff; сохранить plan и immutable desired snapshot со статусом PLANNED.
7. Выбрать фрагмент, который одновременно укладывается не более чем в 15 логических item changes и 15 mutating HTTP requests на playlist за окно: remove нежелательных, add отсутствующих, затем move для порядка. Batch add/remove считается одним HTTP request, но каждый затронутый item расходует item-change budget; каждый move требует отдельного request. Planner останавливается до превышения любого из двух лимитов.
8. Для move использовать `edit_playlist(moveItem=(setVideoId, beforeSetVideoId))`: `setVideoId` берётся только из свежего `get_playlist()`. Стабильный left-to-right planner фиксирует каждый перемещённый элемент на окончательной позиции.
9. Применить выбранные remove/add/move; статус WRITING. Повторно прочитать плейлист; статус VERIFYING.
10. Если remote совпал с ожидаемым промежуточным snapshot, но ещё не с desired, сохранить PARTIAL и продолжить тот же plan в следующее publish-окно. При полном совпадении — COMPLETE. Любое неожиданное расхождение — FAILED без слепого retry.

Фиксация desired snapshot гарантирует сходимость: даже если новый recommendation уже рассчитан, unfinished plan не гонится за ежедневно меняющейся целью. В худшем случае сложная перестановка сходится несколькими 24-часовыми окнами; scheduler продолжает PARTIAL без нового listening signal, а UI отображает оставшиеся item changes, requests и target generation.

Rollback — отдельная ручная операция из backup. Автоматический rollback может усугубить частично успешную запись и поэтому не запускается без проверки.

## 9. Внутренний call budget

Это не официальный quota, а предохранитель приложения:

| Категория | Автоматический предел |
| --- | --- |
| Полный library sync | 4 раза в сутки |
| Candidate refresh | 1 раз в сутки, максимум 6 seed |
| Frontier expansion графа | каждые 6 часов, максимум 12 узлов за прогон (по 1 вызову radio) |
| Все discovery-вызовы (`get_watch_playlist`, `get_song_related`) | максимум 120 в сутки суммарно; расширение графа никогда не вытесняет публикацию |

Потолок discovery-вызовов существует, чтобы зациклившийся job не долбил YouTube, а не чтобы экономить на исследовании: покрыть каждый лайк хотя бы одним запросом стоит один вызов на трек, и при шестидесяти корнях меньший лимит растянул бы представление всего вкуса на двое суток.
| Remote publish | 1 publish-окно в сутки |
| Первичное создание managed playlist | один подтверждённый create с максимум `configured_target_size` initial IDs и до трёх verify reads; максимум 4 playlist-endpoint requests на playlist |
| Последующие item changes одного managed playlist | максимум 15 логических item changes за окно |
| Последующие mutating requests одного managed playlist | максимум 15 за окно; batch add/remove = 1 request, move = 1 request |
| Все playlist-endpoint requests одного managed playlist | максимум 17 за окно: 1 fresh read + до 15 mutations + 1 verify read |
| Глобальный automatic playlist budget | максимум 51 playlist-endpoint request за сутки для трёх managed playlists; jobs выполняются последовательно |
| Ручной cleanup setup artifact | максимум 2 playlist-endpoint requests на одно явное подтверждение: 1 fresh marker read + 1 `delete_playlist`; не входит в automatic 51, но учитывается ledger/circuit и не повторяется вслепую |
| Повтор после transient error | 60 секунд → 5 минут → 30 минут → circuit open |
| Search | только пользовательский ввод, debounce + cache |

Initial setup является отдельной явной операцией: максимум 12 endpoint requests для трёх playlists (create плюс до трёх verification reads на каждый), все заносятся в ledger; automatic publish в те же сутки не запускается. Каждый внешний вызов записывается в `api_call_ledger` с playlist/publication ID и признаком read/mutation. При превышении item, per-playlist request или global request budget automatic jobs откладываются; ручное force-действие требует отдельного подтверждения в UI и не обходит request caps или circuit breaker при auth/rate-limit ошибке.

## 10. Стратегия обновления зависимости

- В lockfile используется точная версия, без плавающего диапазона.
- Не обновлять автоматически Dependabot-merge без проверки.
- Перед upgrade выполнить adapter contract tests на записанных обезличенных fixtures.
- Затем выполнить read-only smoke на реальном аккаунте.
- Write smoke выполняется только на отдельном тестовом private playlist.
- При parser-регрессии вернуть предыдущий образ; миграция БД не должна зависеть от новой формы внешнего payload.
- Проверять PyPI, changelog, свежесть `main`, открытые regression issues и stable setup docs.
