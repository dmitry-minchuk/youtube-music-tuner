# YouTube Music Tuner

Локальный персональный музыкальный плеер и рекомендательная система поверх YouTube Music. Приложение должно учиться на лайках, пропусках, дослушиваниях, повторах и другом взаимодействии, формировать «волну» с управляемой температурой и аккуратно публиковать несколько приватных плейлистов обратно в YouTube Music.

## Статус

Документация приведена к revision 1.2.1, реализованы фазы 0–6 из [roadmap](docs/12-roadmap.md): backend, frontend, интеграция с YouTube Music, плеер с телеметрией, рекомендатель, online-обучение, безопасная публикация и обслуживание. Приложение запускается одной командой; реальный end-to-end прогон на живом аккаунте (`REAL_YTM_TESTS=1`) ещё не выполнялся.

Что уже работает:

- health/readiness, миграции, JSON-логи с редакцией секретов;
- OAuth device flow через CLI, синхронизация лайков/плейлистов/истории;
- собственный плеер поверх YouTube IFrame с честным подсчётом прослушанного;
- «Волна» с температурой, mood и объяснениями выбора;
- LinUCB в shadow-режиме с активацией после чистого baseline;
- создание и публикация трёх приватных плейлистов Tuner с backup и verify;
- ежедневный backup, retention и бюджеты внешних вызовов.

## Зафиксированные решения

- приложение однопользовательское, локальное и некоммерческое;
- запуск — одним Docker Compose-сервисом на `http://127.0.0.1:43127`;
- собственный React-интерфейс, а воспроизведение — через видимый YouTube IFrame Player;
- интеграция библиотеки и плейлистов — через `ytmusicapi`, закреплённую на проверенной версии;
- телеметрия хранится локально в SQLite и отправляется на backend пакетами;
- основной рекомендатель — локальный contextual bandit с явными правилами разнообразия;
- внешняя или локальная LLM для основной рекомендации не нужна;
- первые 100 квалифицированных прослушиваний обслуживает rule-ranker; LinUCB учится в SHADOW с 40-й сессии и влияет на очередь только после baseline и safety gates;
- публикация использует adaptive playlist size 25–60, crash-safe CREATING/UNVERIFIED manifest и пишет только в ACTIVE плейлисты Tuner;
- каждый последующий publish ограничен 15 изменёнными элементами, 15 mutating requests и 24-часовым окном; PARTIAL завершается без требования новых прослушиваний.

## Документация

Навигация и рекомендуемый порядок чтения находятся в [docs/README.md](docs/README.md).

Ключевые документы:

- [продуктовые требования](docs/01-product-requirements.md);
- [системная архитектура](docs/02-system-architecture.md);
- [интеграция с YouTube Music](docs/03-youtube-integration.md);
- [рекомендательный движок](docs/05-recommendation-engine.md);
- [эксплуатация в Docker](docs/09-operations-docker.md);
- [риски и приватность](docs/10-security-privacy-risks.md);
- [план разработки](docs/12-roadmap.md).

## Запуск

```bash
docker compose up --build -d
curl --fail http://127.0.0.1:43127/health/ready
```

Интерфейс доступен только на loopback-адресе:

```text
http://127.0.0.1:43127
```

Порт меняется через `APP_PORT` в `.env`, образ пересобирать не нужно.

### Подключение к YouTube Music

Собственный Google Cloud OAuth client проверен и **не работает**: device flow проходит, но YouTube Music отвечает `HTTP 400` на любой запрос с таким токеном. Рабочий способ — browser authentication.

**Проще всего — кнопкой в интерфейсе:** откройте http://127.0.0.1:43127 → Settings → **Connect**. Панель покажет три шага (открыть music.youtube.com, скопировать request headers в DevTools, вставить) и проверит их реальным запросом.

**Одной командой в терминале:**

```bash
./connect.sh
```

Скрипт откроет окно браузера, дождётся логина, сам заберёт заголовки, импортирует их и затрёт временный файл.

Оба пути подтверждают успех только после реального ответа YouTube Music. Подробности и резервный OAuth-путь: [docs/03](docs/03-youtube-integration.md).

Дальше в Settings нажать «Sync now», затем на экране Playlists сделать preview и создать три плейлиста Tuner.

### Обслуживание

```bash
# Проверенный backup с манифестом и integrity check
docker compose exec tuner python -m app.cli backup

# Скопировать backup из named volume на хост (только так он становится внешним)
docker compose cp tuner:/data/backups/<stamp> ./backups/<stamp>

# Логи без follow
docker compose logs --tail=200 tuner

# Остановка без удаления данных
docker compose down
```

`docker compose down -v` удалит volume вместе с БД, OAuth и backup-копиями — не использовать как обычную команду.

## Разработка

```bash
python3 -m venv .venv && .venv/bin/pip install -e "./backend[dev]"
cd backend && ../.venv/bin/python -m pytest -q          # 215 тестов
../.venv/bin/ruff check . && ../.venv/bin/mypy app

cd frontend && npm install && npm run build && npx vitest run
```

Локальный запуск без Docker:

```bash
cd backend && TUNER_DATA_DIR=../data ../.venv/bin/alembic upgrade head
TUNER_DATA_DIR=../data ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 43127
cd frontend && npm run dev    # Vite на 43128 с проксированием /api
```

## Структура

```text
backend/app/
  api/            HTTP-роуты, DTO, guard-middleware, маппинг ошибок
  domain/         типы каталога, не зависящие от ytmusicapi
  integrations/   адаптер ytmusicapi, ledger, бюджеты, circuit breaker
  player/         агрегация сессий и формула reward
  recommender/    признаки, rule-ranker, LinUCB, reranker, температура
  publishing/     quality gates, diff planner, state machine публикации
  jobs/           очередь с lease, scheduler, sync/train/maintenance
  persistence/    модели SQLAlchemy и репозитории
frontend/src/
  player/         iframe-адаптер, трекер прослушивания, outbox телеметрии
  features/       wave, library, playlists, insights, settings
```

## Важное ограничение

`ytmusicapi` — неофициальная библиотека, повторяющая запросы веб-клиента YouTube Music. Для личного инструмента риск принят, но адаптер должен быть изолирован, вызовы — ограничены, а обновление зависимости — проходить контрактные smoke-тесты. Подробности: [ADR-001](docs/decisions/ADR-001-use-ytmusicapi.md).
