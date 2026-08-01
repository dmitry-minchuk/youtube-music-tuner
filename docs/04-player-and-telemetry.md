# Плеер и телеметрия

## 1. Принцип воспроизведения

Frontend встраивает официальный YouTube IFrame Player и управляет им через JavaScript API. Tuner передаёт только `videoId`; не извлекает URL потока, не проксирует media и не сохраняет аудио.

Плеер остаётся видимым, не перекрывается интерфейсом и имеет viewport не менее 200×200 px. Рекомендуемый desktop-размер — 480×270. В iframe задаётся точный `origin=http://127.0.0.1:43127` с учётом настраиваемого порта.

Собственная нижняя панель даёт быстрые play/pause/next/previous/like/dislike/volume, но не маскирует обязательные элементы iframe. Автовоспроизведение начинается только после пользовательского действия с учётом browser autoplay policy.

При переходе документа в `hidden` Tuner по умолчанию ставит player на паузу и фиксирует нейтральное `visibility_changed`. Это намеренный compliance trade-off, а не оптимизация телеметрии: [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies) запрещают background player и определяют его как player, который не отображается в странице, вкладке или экране, просматриваемом пользователем. Неактивная вкладка попадает под это определение.

Практика внесла уточнение: браузеры сообщают `hidden` и тогда, когда окно просто перекрыто другим приложением (occlusion на macOS), а это строже, чем требует политика — пользователь физически смотрит на другое окно, но player не спрятан и не замаскирован. Поэтому поведение вынесено в настройку `Settings → Playback → Pause when this tab is hidden`:

- значение по умолчанию — включено, то есть policy-safe;
- выключение — осознанный выбор владельца локальной установки, который принимает на себя связанный policy-риск;
- состояние хранится в `settings` и применяется к плееру при загрузке UI.

## 2. PlayerPort

UI вне adapter работает с контрактом:

```typescript
interface PlayerPort {
  cue(videoId: string): Promise<void>;
  play(): void;
  pause(): void;
  seekTo(seconds: number): void;
  setVolume(percent: number): void;
  currentTime(): number;
  duration(): number;
  state(): PlayerState;
  subscribe(listener: (event: PlayerEvent) => void): () => void;
}
```

Adapter переводит YouTube state codes в `UNSTARTED | ENDED | PLAYING | PAUSED | BUFFERING | CUED | ERROR`.

## 3. Словарь событий

| Событие | Когда создаётся | Влияет на reward |
| --- | --- | --- |
| `track_cued` | iframe подготовил новый `videoId` | нет |
| `play_started` | первый переход в PLAYING | начало сессии |
| `play_resumed` | PLAYING после pause/buffer | нет отдельно |
| `paused` | явная/iframe пауза | нейтрально |
| `buffering_started` | state BUFFERING | нейтрально |
| `progress_tick` | каждые 15 секунд активной сессии; содержит cumulative counters | heartbeat, сохраняет прогресс, сам по себе нейтрален |
| `seek_forward` | позиция скачком вперёд | снижает достоверность completion |
| `seek_backward` | позиция скачком назад | слабый положительный сигнал |
| `next_clicked` | пользователь явно нажал next/выбрал другой трек | классифицирует skip |
| `previous_clicked` | пользователь вернулся | слабый положительный для целевого трека |
| `ended` | естественный конец | положительный completion |
| `replay_started` | тот же трек запущен повторно в течение 10 минут | сильный положительный |
| `like_set` | явная оценка | сильный положительный |
| `dislike_set` | явная оценка | сильный отрицательный, block |
| `player_error` | iframe вернул ошибку (например, 150 — встраивание запрещено правообладателем) | нейтрально для вкуса; закрывает сессию с `termination_reason=player_error` и включает 24-часовой cooldown кандидата |
| `visibility_changed` | visible/hidden | диагностический |
| `page_closing` | `pagehide`/закрытие | нейтрально |

## 4. Как считается реальное время

`played_seconds` нельзя вычислять как максимальный `currentTime`: перемотка вперёд ложно создаст дослушивание.

Frontend раз в секунду снимает monotonic clock и позицию player. Интервал добавляется к `played_seconds`, только если:

- предыдущий и текущий state — PLAYING;
- вкладка не была frozen;
- monotonic delta находится в диапазоне 0–2.5 секунды;
- изменение player position согласуется с естественным воспроизведением, а не seek;
- нет активного buffering.

Дополнительно хранятся `max_position_seconds`, `seek_forward_seconds`, `seek_backward_seconds`, `seek_forward_count`, `seek_backward_count`, `buffered_seconds` и `wall_clock_seconds`.

Эффективная длительность определяется в таком порядке:

1. конечное положительное значение `YT.Player.getDuration()` после READY/PLAYING — `duration_source=PLAYER` и наиболее авторитетный источник;
2. положительная metadata duration из каталога — `duration_source=METADATA`;
3. иначе `effective_duration_seconds=NULL`, `duration_source=UNKNOWN`.

Если IFrame позднее отдаёт duration, текущая сессия и track cache обновляются; расхождение player/metadata более двух секунд логируется как безопасная diagnostics-метрика, а для классификации используется PLAYER. При известной duration position ограничивается `duration + 5` секундами. При неизвестной duration backend не применяет этот cap: он ограничивает `played_seconds` суммой проверенных monotonic PLAYING-интервалов и `wall_clock_seconds + 5`, поэтому большой `positionSeconds` не способен создать прослушивание.

## 5. Playback session

Сессия идентифицируется UUID и относится к одному `videoId`. Она начинается при первом PLAYING и закрывается при:

- естественном ENDED;
- явном переходе на другой трек;
- ошибке без восстановления в течение 30 секунд;
- отсутствии `progress_tick`/state-transition heartbeat более 2 минут;
- повторном старте того же трека после закрытия предыдущей сессии.

Закрытие вкладки само по себе не означает skip. Незавершённая сессия получает `termination_reason=abandoned_unknown` и не даёт отрицательного reward.

Квалифицированная сессия: минимум 10 фактически проигранных секунд либо явный like, dislike или Next. Осознанный Next до десятой секунды одновременно закрывает сессию, квалифицирует её и даёт соответствующий early-skip reward; автоматический переход, player error, pause, hidden и закрытие вкладки этого не делают.

## 6. Доставка событий

- Каждое событие имеет `client_event_id` UUID, `session_id`, `sequence_no`, `occurred_at`, `monotonic_ms`, `video_id`, `payload`, `schema_version=1`.
- Frontend накапливает batch в памяти и IndexedDB до подтверждения backend.
- Каждые 15 секунд активной сессии создаётся `progress_tick` с cumulative `played_seconds`, `position_seconds`, effective duration, seek/buffer counters и текущим state. Cumulative форма позволяет безопасно пережить повтор или пропущенный batch без двойного суммирования.
- Сразу после создания tick batch отправляется; дополнительные flush выполняются при смене трека, like/dislike, pause и visibility change.
- На `pagehide` используется `navigator.sendBeacon`, если доступен.
- Backend принимает события повторно безопасно; unique constraint на `client_event_id`.
- Gap в `sequence_no` отмечается, но не блокирует последующие события.
- Server timestamp хранится отдельно от client timestamp.
- При crash без `pagehide` теряется не более текущего интервала после последнего созданного tick — максимум около 15 секунд; уже созданные неподтверждённые ticks остаются в IndexedDB и отправляются после reload.

## 7. Вывод поведенческих сигналов

Backend агрегирует события в session summary. При известной duration:

- `played_ratio = min(played_seconds / effective_duration_seconds, 1)`;
- `early_skip = explicit_next AND played_ratio < 0.20`;
- `mid_skip = explicit_next AND 0.20 <= played_ratio < 0.60`;
- `large_forward_seek = seek_forward_seconds >= max(30, 0.20 * effective_duration_seconds)`;
- `completed = played_ratio >= 0.90 OR (ended AND played_ratio >= 0.70 AND NOT large_forward_seek)`.

При неизвестной duration `played_ratio=NULL`, а `classification_basis=ABSOLUTE_TIME`:

- explicit next при `played_seconds < 30` → `early_skip=true`, менее уверенный raw reward `-2`;
- explicit next при `30 <= played_seconds < 120` → `mid_skip=true`, raw reward `-0.5`;
- explicit next после 120 секунд → нейтрально по completion/skip;
- `large_forward_seek = seek_forward_seconds >= 30`;
- ENDED даёт completion только при `played_seconds >= 60` и отсутствии large forward seek; иначе `ended_unqualified=true` и reward нейтрален.

Также сохраняются `replayed`, `seek_backward_count`, `explicit_rating`, `qualified`, `reward`, `reward_version` и `classification_basis=RATIO|ABSOLUTE_TIME`. Если duration становится известна позже и raw events ещё находятся в retention, сессия пересчитывается по RATIO.

Правила reward версионируются. Raw events позволяют детерминированно пересчитать только сессии внутри 180-дневного retention; более старые summaries сохраняют исходные `reward`/`reward_version`. Новая формула применяется к retained окну и новым событиям, не переписывая исторический baseline задним числом.

## 8. Очередь

- Frontend держит queue snapshot с `queue_id`, порядком и `generation_id`.
- За 3 трека до конца запрашивает локальное продолжение, не внешний YouTube endpoint.
- Next атомарно закрывает текущую сессию перед запуском следующей.
- Previous возвращает последний реально запущенный трек, а не просто предыдущую строку snapshot.
- Если iframe не может проиграть candidate, создаётся neutral error и выбирается следующий. Код ошибки различается: `101`/`150` означают запрет встраивания правообладателем — это свойство трека, поэтому он получает `is_playable=false` и больше не попадает в очередь; остальные коды дают обычный 24-часовой cooldown. Первый прогон живой библиотеки показал 29 таких треков из 51 лайка, и без этого различия они пропускались бы в каждой очереди.
- Автоматический переход после `ended` или `player_error` **не отправляет** `next_clicked`: событие явного пропуска создаётся только действием пользователя. Иначе непроигрываемый трек получал бы отрицательный reward, как будто его отвергли.
- Queue history не перетасовывается при изменении температуры; новое значение действует на ещё не проигранный хвост.

## 9. Что видно при прослушивании вне Tuner

В официальном YouTube Music Tuner не видит точную позицию, паузы, пропуски и причину остановки. Периодический `get_history()` может дать факт недавнего воспроизведения, но такой сигнал маркируется `source=remote_history`, получает малый вес recency и не используется как negative feedback.

Лайки, поставленные в официальном приложении, попадут при следующем library sync и будут сильным положительным сигналом. Именно поэтому управляемые плейлисты полезны на iPhone, но максимальная точность обучения достигается при прослушивании через собственный web-player.
