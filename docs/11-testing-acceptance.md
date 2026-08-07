# Тестирование и приёмка

## 1. Стратегия

Тестовая пирамида:

- unit: domain rules, reward, ranking, diff, cooldown, telemetry aggregation;
- contract: ytmusicapi adapter на fixtures;
- integration: FastAPI + временная SQLite + fake music provider;
- frontend component: player state и UI states;
- E2E: browser с fake iframe/player port;
- real smoke: минимальный набор на настоящем аккаунте и отдельном тестовом private playlist.

Реальные write-тесты никогда не запускаются обычной CI-командой.

## 2. Unit tests

### Telemetry

- forward seek не увеличивает `played_seconds`;
- buffering/pause не считается проигрыванием;
- duplicated event игнорируется;
- out-of-order batch сортируется по sequence при допустимом gap;
- `progress_tick` каждые 15 секунд восстанавливает cumulative progress без двойного суммирования; crash теряет не более одного интервала;
- page close не создаёт early skip;
- explicit next до 20% создаёт negative signal;
- explicit next раньше 10 секунд всё равно квалифицирует сессию; автоматический переход/error раньше 10 секунд — нет;
- duration берётся из PLAYER раньше METADATA; при полном отсутствии duration применяются точные 30/120-секундные fallback rules;
- `large_forward_seek` срабатывает на `max(30 секунд, 20% duration)`, а ended после такого seek не получает ложный completion;
- like/dislike доминирует над implicit reward;
- completion `+2` не суммируется с partial-listen `+1`, а достижимый implicit clamp равен `[-3, 6]`;
- `reward=tanh(raw/4)` различает completion, replay, like и проверяется с tolerance `1e-3`;
- изменение reward version пересчитывает только retained raw events и не переписывает замороженный baseline/старые summaries.

### Recommendation

- explicit dislike отсутствует во всех temperature;
- фиксированный random seed воспроизводит очередь при одинаковом состоянии базы; повторный вызов подряд намеренно даёт другую волну;
- две соседние волны пересекаются не более чем на 30%, discovery-часть не повторяется;
- сэмплирование само по себе меняет порядок: два разных rng дают разные очереди, отсутствие rng — детерминированную;
- temperature увеличивает discovery quota монотонно;
- один артист не нарушает window limits;
- empty mood pool безопасно расширяется;
- недостаток familiar pool возвращает фактический mix и `FAMILIAR_POOL_WIDENED`, не нарушая recent-track filter;
- квота двусторонняя: при достаточных обеих сторонах пула фактический mix равен целевому с точностью округления, а перебор familiar возможен только после исчерпания admissible discovery и сопровождается `DISCOVERY_POOL_WIDENED`;
- `Rediscover` ограничивает выбор треками, не звучавшими не меньше 60 дней, при нехватке деградирует до soft boost с `CONTEXT_WIDENED`; контексты без mood-данных не меняют выдачу и всегда возвращают `CONTEXT_WIDENED`;
- «тёплый» трек перестаёт числиться знакомым после 60 дней без прослушивания и классифицируется как discovery;
- старт трека отмечает его `played_at` на волне, которая его выдала; ретюн заменяет только непроигранный хвост очереди; за 3 трека до конца очередь продолжается локальным extend без внешнего вызова;
- отрицательная artist affinity штрафует кандидатов пропорционально уверенности (`min(1, plays_all/3)`); лайки не штрафуются; штраф за veto-артиста и штраф за отрицательную affinity не суммируются (max);
- veto исключает трек из пула, топит остальные не-лайки артиста (со сниженным штрафом для знакомых с собственным позитивом), топит графовых соседей, обнуляет seed-вес и закрывает узел для раскрытия; publishing не содержит не-лайков veto-артистов; снятие veto возвращает всё, кроме уже записанных сессий (история не переписывается);
- дизлайк автоматически переключает на следующий трек; негативный сигнал (дизлайк, veto, explicit next раньше 60% / 120 с) перестраивает непроигранный хвост очереди локальным retune с дебаунсом, не трогая проигранную голову; поздний explicit next хвост не перестраивает;
- рёбра с истёкшим `expires_at` остаются в пуле; поддержка считает все seed, а не лучшее ребро;
- расширение графа уходит на хоп дальше, пропускает уже раскрытые узлы и останавливается при исчерпании discovery-бюджета;
- агрегаты affinity идемпотентны к повторной агрегации сессии и обновляются на ingest телеметрии;
- лайк не дисконтируется за появление в чужом radio: его оценка равна оценке лайка вне графа;
- нераскрытый лайк опережает в границе расширения любой уже достижимый кандидат, каким бы поддержанным тот ни был;
- все избранные раскрываются раньше, чем обход уходит глубже;
- desired list проходит гейты по построению — квота, лимит на артиста, отсутствие соседних одинаковых артистов, минимум разных артистов и порог качества;
- режим публикации не применяет окна новизны и историю волн, поэтому знакомых треков в нём больше, чем в потоковой волне;
- провалившаяся периодическая работа не ставится заново до истечения backoff; успешное обучение без нового snapshot тоже ждёт; открытый circuit не откладывает локальные работы;
- NaN snapshot не активируется;
- bootstrap не запускает SHADOW раньше всех четырёх порогов; сессии 1–100 всегда обслуживает неизменный rule ranker, ACTIVE невозможен до 100;
- baseline/SHADOW playlist gate использует `quality_expected=2*rule_score-1`, ACTIVE — LinUCB exploitation; `rule_score=0.40` проходит ту же границу `-0.20`, а exploration bonus её не обходит;
- ranking response однозначно различает serving rule policy, nullable shadow model и ACTIVE serving model;
- training watermark не использует одну session дважды.

### Publishing

- отсутствующий размер плейлиста сохраняется как NULL, а не 0; Liked Music отдаёт локальное число лайков;
- preview возвращает треки с названием, артистами и familiarity, а не только счётчики;
- повторный preview возвращает тот же список, `regenerate` — другой, и он становится новым предложением;
- publish пишет просмотренный список, а не заново сгенерированный;
- ACTIVE-плейлист удаляется по явному подтверждению, любой другой статус — отклоняется;
- verification повторяет чтение, пока только что созданный playlist не станет читаемым, и сохраняет ID при исчерпании попыток;
- verification сверяется с сохранённым desired hash, поэтому повторный adopt проходит, а изменённый извне playlist по-прежнему отклоняется;
- не-managed playlist всегда отклоняется;
- marker mismatch всегда отклоняется;
- remote hash conflict не пишет ничего;
- initial create до external call сохраняет CREATING intent, сразу после ответа сохраняет ID как UNVERIFIED и только после полного verify делает его ACTIVE;
- потеря ответа/crash/verify failure не создаёт duplicate playlist: restart reconciles exact instance marker, а ambiguous matches требуют cleanup;
- cleanup может удалить только UNVERIFIED/CLEANUP_REQUIRED exact ID с совпавшим marker и явным подтверждением;
- успешный cleanup вызывает один fresh read и один delete, переводит manifest в DELETED; marker mismatch не вызывает delete, а неоднозначный ответ оставляет CLEANUP_REQUIRED без blind retry;
- initial create передаёт `effective_target_size` IDs одним подтверждённым вызовом и верифицирует полный порядок;
- каждый playlist quality gate имеет детерминированные pass/fail fixtures, а failure не создаёт внешний вызов;
- planner выбирает максимальный feasible `effective_target_size`: при достаточном остальном pool 30 familiar в Familiar policy дают 42 и `TARGET_SIZE_REDUCED_FOR_POOL`; размер меньше 25 возвращает `INSUFFICIENT_POOL` с точными required/available counts и без внешнего вызова;
- unique primary artists не меньше `ceil(0.40 * effective_target_size)` при max 3/artist и отсутствии adjacent same artist;
- subsequent diff минимален и одновременно ограничен 15 логическими item changes и 15 mutating HTTP requests;
- batch add/remove расходует один request и item count по числу IDs; каждый move расходует один request; вместе с fresh read/verify не превышается 17 endpoint requests на playlist и 51 в сутки глобально;
- PARTIAL publication продолжает прежний desired hash без 15 новых sessions, ждёт следующее 24-часовое окно и left-to-right move plan сходится к точному порядку за конечное число окон;
- PARTIAL перед каждым продолжением повторно проверяет dislike/unavailable, ownership marker, remote hash, circuit и бюджеты; safety invalidation отменяет plan;
- повтор idempotency key не создаёт вторую публикацию;
- restart в WRITING приводит к fresh read/verify, не к повтору add.

## 3. Contract tests ytmusicapi

Adapter fixtures должны покрывать:

- liked track с/без album/duration;
- несколько artists;
- playlist continuation;
- unavailable/deleted item;
- localized metadata;
- related/radio empty response;
- auth/rate-limit/parse errors;
- create-with-videoIds/add/remove/edit-move/delete success shapes, включая обязательный `setVideoId` и `delete_playlist` status/full-error response.

Fixtures очищаются от OAuth, cookies, account identity и private playlist IDs. Contract tests проверяют наши typed results, а не полную копию внутреннего response.

При обновлении `ytmusicapi` тесты запускаются сначала на старых fixtures, затем read-only recorder обновляет только намеренно изменившиеся fixtures после ручного review.

## 4. API integration

- migrations применяются на пустой и предыдущей схеме;
- telemetry batch частично принимает valid events;
- pagination stable;
- sync upsert не стирает local sessions;
- job lease работает при двух test workers;
- circuit breaker блокирует auto jobs;
- быстрые rating-переключения используют один `rating:{video_id}` key; финальное remote состояние соответствует максимальной revision;
- cleanup endpoint требует exact ID/marker/confirmation, создаёт не более двух ledger entries и только подтверждённый outcome возвращает DELETED;
- secrets не появляются в captured logs/errors;
- CSP/CORS/Origin/CSRF guards активны, неизвестный Host отклоняется как DNS-rebinding attempt;
- backup проходит SQLite integrity check.

## 5. Frontend/E2E

Fake `PlayerPort` воспроизводит ready/playing/buffering/paused/ended/error и управляемую позицию. Playwright проверяет:

- onboarding без OAuth;
- Wave start одним явным действием;
- восстановление queue после reload;
- температура меняет только будущий хвост;
- offline/stale UI остаётся читаемым;
- rejected telemetry повторяется без дублирования accepted;
- like → pending sync → synced/error;
- keyboard navigation и visible focus;
- iframe panel не меньше минимального размера на supported desktop viewport;
- переход вкладки в hidden ставит player на паузу и не создаёт negative reward;
- destructive dialog описывает точный scope.

## 6. Real-account smoke

Выполняется вручную с флагом `REAL_YTM_TESTS=1`:

1. `get_account_info` возвращает ожидаемый аккаунт.
2. Прочитать первые лайки и один существующий playlist без записи.
3. Получить related/radio для одного seed.
4. Воспроизвести один `videoId` через браузер и получить state events.
5. Создать отдельный private playlist `Tuner Test <date>` одним `create_playlist(..., video_ids=[id1, id2])`.
6. Прочитать и верифицировать оба track ID и их порядок.
7. Переместить второй item перед первым через `moveItem=(setVideoId2, setVideoId1)` и повторно верифицировать порядок.
8. Удалить только этот test playlist после явной проверки ID/marker.

Последний шаг деструктивный и не входит в автоматический default run. Managed production playlists в smoke не используются.

## 7. Acceptance scenarios

| ID | Сценарий | Ожидаемый результат |
| --- | --- | --- |
| AT-01 | Fresh Docker start | `/health/ready` green, UI на `127.0.0.1:43127`, данные persistent |
| AT-02 | OAuth connect | аккаунт виден, токены не попали в UI/logs |
| AT-03 | Initial sync | лайки/плейлисты кешированы, refresh экрана не вызывает новые YTM calls |
| AT-04 | Play through UI | видимый iframe играет, custom controls и queue работают |
| AT-05 | Early explicit next | корректный played time и negative session reward |
| AT-06 | Buffer/close/hidden | negative signal отсутствует; hidden дополнительно ставит player на policy-паузу |
| AT-07 | Bootstrap и baseline | до 40 — только rule ranker; 40–99 — rule serving + SHADOW; ACTIVE возможен после чистых 100 и safety gates |
| AT-08 | Temperature | первые 20 элементов соответствуют familiar/discovery quota и diversity |
| AT-09 | Autogeneration | 15 новых sessions запускают training/new plan без участия пользователя; PARTIAL продолжается без нового сигнала после cooldown |
| AT-10 | Safe publish | crash-safe create+verify adaptive size; далее только ACTIVE managed playlist, gates, backup, convergent PARTIAL с item/request caps |
| AT-11 | Restart during job | нет duplicate writes, состояние восстанавливается |
| AT-12 | Official iPhone app | три private Tuner playlists видны и воспроизводятся в YouTube Music |

## 8. Quality thresholds перед MVP

- 100% unit coverage для ownership guard, diff cap и reward classification branches;
- не менее 90% branch coverage domain/recommender в целом;
- zero high-severity dependency vulnerabilities либо документированное исключение;
- zero secrets по secret scanner;
- все AT-01…AT-12 пройдены и записаны с датой/app version;
- baseline и post-bootstrap метрики считаются на реальных sessions без ручной подмены;
- 24-часовой soak test не создаёт лишних sync/publish calls.

## 9. Наблюдение после запуска

Первые две недели auto-publish лучше держать в preview-only или включать после ручной проверки каждого plan. Проверяются:

- false skips из-за UI/player transitions;
- превышение call budget;
- repeated parse errors;
- artist concentration;
- расхождение playlist verification;
- распределение `rule_score` и доля кандидатов, проходящих `rule_score >= 0.40`, отдельно по Familiar/Balance/Discovery; это диагностика некалиброванного mapping, а не основание автоматически менять порог;
- размер WAL и успешность daily backup.
