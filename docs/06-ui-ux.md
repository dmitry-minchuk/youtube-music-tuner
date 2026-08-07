# UI/UX

## 1. Направление

Интерфейс использует сильные стороны Яндекс Музыки как референса — спокойную иерархию, минимум визуального шума, быстрый вход в персональный поток, крупные обложки и постоянный плеер. Брендинг, точная композиция экранов, цвета, иконки и microcopy не копируются.

Собственная визуальная идентичность:

- тёмный графитовый фон, а не чистый чёрный;
- акцент `warm coral` для primary action и `cool teal` для discovery/temperature;
- мягкие поверхности без чрезмерных рамок;
- большие интервалы и одна главная цель на экран;
- шрифт системный, без внешней font-зависимости в MVP.

## 2. Информационная архитектура

Левое меню:

- `Wave` — главный экран;
- `Collection` — лайки и локально известные треки;
- `Playlists` — удалённые и managed playlists;
- `Insights` — обучение, сигналы и качество;
- `Settings` — подключение, sync, automation, privacy.

Постоянные области:

- центральный контент;
- видимая область YouTube player минимум 200×200;
- нижняя player bar;
- компактный status indicator sync/API только при проблеме или активной операции.

## 3. Desktop wireframe

```text
┌──────────────┬──────────────────────────────────────┬────────────────────┐
│ TUNER        │ Good evening                         │ Now playing        │
│              │                                      │ ┌────────────────┐ │
│ ● Wave       │  Your Wave                           │ │ YouTube iframe │ │
│   Collection │  [ Focus ▾ ]                         │ │  visible       │ │
│   Playlists  │                                      │ └────────────────┘ │
│   Insights   │  Familiar ━━━━━●━━━━ Discovery       │ Title              │
│   Settings   │                                      │ Artist             │
│              │  [▶ Start Wave]  [Why this mix?]     │ Why: rediscovery   │
│              │                                      │                    │
│              │  Up next                             │ Learning 73 events │
│              │  01 Track — Artist        3:42       │                    │
│              │  02 Track — Artist        4:03       │                    │
└──────────────┴──────────────────────────────────────┴────────────────────┘
┌──────────────────────────────────────────────────────────────────────────┐
│ ♡  artwork  Title — Artist       ◀  ▶/Ⅱ  ▶       ━━━━━━━  🔊   ···      │
└──────────────────────────────────────────────────────────────────────────┘
```

На ширине 1024–1279 px правая панель превращается в раскрываемый Now Playing drawer, но iframe при воспроизведении остаётся доступным и достаточного размера.

## 4. Wave

Первый viewport содержит только:

- приветствие/контекст;
- mood selector;
- temperature slider с подписями `Familiar` и `Discovery`;
- один primary button `Start Wave` или `Resume`;
- первые элементы `Up next`;
- краткий learning/status label.

Temperature slider показывает ожидаемый состав, например `55% familiar · 45% discovery`, а не абстрактное число. Изменение не перестраивает уже проигранную историю, только будущий хвост.

У каждого трека доступен `Why this?` с 1–3 рассчитанными причинами без генеративного текста:

- `Liked artist, new track`;
- `Related to …`;
- `You usually finish this artist`;
- `Not played for 8 months`;
- `Discovery pick with high uncertainty`;
- `ARTIST_VETOED` → «By an artist you marked "not my thing"» — трек прошёл в волну несмотря на штраф артиста, и это не скрывается.

В плеере рядом с `Like`/`Dislike` есть третья кнопка `Not my thing` («Don't Like At All», docs/05 §11): она ставит локальный veto на трек, его артиста и графовую окрестность и сразу переключает на следующий трек. Повторное нажатие снимает veto. В отличие от `Dislike`, сигнал никогда не уходит в YouTube.

## 5. Collection

- вкладки `Liked`, `Recently played`, `Discovered`, `Blocked`;
- поиск/фильтр работает по локальному каталогу;
- строки показывают local affinity, last played и sync state только по запросу `Details`, чтобы не перегружать основной список;
- bulk editing внешней библиотеки не входит в MVP.

## 6. Playlists

Две секции:

- `Tuner playlists` — Familiar, Balance, Discovery с lifecycle status, configured/effective size, last publish, pending diff и кнопками Preview/Publish now/Regenerate/Delete;
- `Your YouTube Music playlists` — read-only карточки и действие `Play in Tuner`. Размер показывается как есть: YouTube не сообщает его для своих системных плейлистов, и такая карточка пишет «size not reported by YouTube», а не «0 tracks». Для Liked Music подставляется локально известное число лайков.

### Preview — это просмотр, а не счётчик

Preview раскрывает **сам список**: позиция, название, артист и метка `known`/`new`. Любую строку можно нажать и услышать её в обычном плеере, кнопка `Play` ставит весь список в очередь. Показанный список фиксируется, и `Publish now` пишет именно его; до открытия Preview кнопка публикации неактивна. `Regenerate` предлагает другой вариант.

Раньше здесь были только счётчики, то есть публикация выполнялась вслепую — это противоречило собственному обещанию «never touched without a preview».

Перед manual publish показываются additions/removals/moves, результаты playlist quality gates, target generation и оставшиеся item changes/HTTP requests при PARTIAL publication. Если target уменьшен из-за pool, UI явно пишет, например `42 of configured 60 · limited by 30 familiar tracks` и reason `TARGET_SIZE_REDUCED_FOR_POOL`. При `INSUFFICIENT_POOL` показываются required/available counts и действия: снизить температуру, уменьшить configured size или накопить больше likes/listens. Причина блокировки отображается отдельно, если quality, cooldown или API circuit не позволяют запись.

CREATING/UNVERIFIED/CLEANUP_REQUIRED не маскируются под готовый playlist. Для них доступны `Verify/adopt` и подтверждённое удаление; обычные Publish/Restore недоступны до ACTIVE. Удаление доступно и для ACTIVE: это плейлист слушателя, и не понравившийся вариант должен убираться из приложения. В обоих случаях диалог показывает точный remote ID, а сервер заново сверяет ownership marker.

Отказ на этапе создания показывается по каждому плейлисту отдельно и человеческим текстом. Отказ по quality gates возвращается обычным `200`, поэтому молчание UI здесь недопустимо: раньше нажатие «Create the three playlists» выглядело как полное отсутствие реакции.

## 7. Insights

Не превращать экран в ML-dashboard. По умолчанию показывать:

- `Collecting signal 31/40 qualified tracks`, затем `Baseline 73/100 · model in shadow`, либо `Model active`; tooltip поясняет, что это прослушанные треки/явные реакции, а не запуски приложения;
- изменение early skip и completion относительно baseline с sample size;
- top positive/negative artists только по локальному поведению;
- знакомое/новое за 7 и 30 дней;
- последнюю/следующую автогенерацию;
- `What Tuner learned` как детерминированные факты.

Панель `Discovery pool` показывает, из чего вообще собирается волна: число играбельных кандидатов, размер графа, распределение по расстоянию от избранного («N at 1 step, M at 2 steps») и процент повтора предыдущей волны. Это те две величины, ради которых существует граф: пул, который растёт, и волны, которые не повторяются.

Расширенный диагностический drawer содержит model version, event counts, call ledger и расход дневного бюджета discovery-вызовов.

## 8. Settings

Группы:

- `YouTube Music`: account, OAuth status, reconnect, last sync;
- `Automation`: auto-train, auto-publish, publish window, default temperature;
- `Playback`: pause-when-hidden (по умолчанию **выключено** — решение владельца от 2026-08-01, см. docs/04 section 1), default temperature, volume, repeat default;
- `Privacy`: event retention, export summary, delete telemetry, disconnect;
- `Diagnostics`: health, database path/size, dependency version, circuit breaker, logs download without secrets.

Опасные действия визуально отделены и требуют явного подтверждения с точным описанием последствий.

## 9. Состояния

Каждый data screen имеет четыре явных состояния:

- loading skeleton при первом локальном чтении;
- empty state с единственным следующим действием;
- stale-but-usable с временем последнего sync;
- error с retry, который уважает cooldown.

Если YouTube недоступен, существующая локальная очередь и Insights остаются доступны. Красный цвет используется только для ошибок и destructive actions, не для обычного dislike control.

## 10. Доступность

- все controls доступны с клавиатуры;
- visible focus ring;
- контраст WCAG AA для текста/контролов;
- temperature доступна arrow keys и имеет `aria-valuetext`;
- иконки имеют accessible labels;
- состояние like/dislike не обозначается только цветом: активная оценка несёт заполненный глиф, рамку, `aria-pressed` и подпись статуса синхронизации (`syncing…`/`synced`/`not synced`);
- каждое действие получает немедленный отклик: оптимистичное состояние кнопки, `:active`-обратная связь, `disabled` пока плеер не готов, индикатор `retuning…` при пересборке хвоста очереди;
- анимации отключаются при `prefers-reduced-motion`;
- hit target минимум 40×40 px.

## 11. Тексты и тон

Короткий, спокойный и честный интерфейс:

- `Learning from 31 qualified listens` вместо `AI is analyzing you`;
- `YouTube Music sync paused — reconnect required` вместо общего `Something went wrong`;
- `No negative signal recorded` при закрытии страницы;
- `Playback paused because this tab is no longer visible` при policy-паузе;
- для нового plan: `Next automatic publish after 21:00, if enough new listens`;
- для незавершённого plan: `Continuing 9 remaining changes after 21:00 · no new listens required`.

UI не обещает знать настроение пользователя и всегда показывает, когда контекст выбран вручную.
