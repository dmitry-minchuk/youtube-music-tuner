# Docker и эксплуатация

## 1. Локальный endpoint

Порт приложения внутри контейнера и default host port: `43127`. На момент архитектурной проверки 2026-08-01 порт не был занят на машине разработчика.

```text
http://127.0.0.1:43127
```

Это намеренно не распространённые 3000, 5000, 8000, 8080, 8888 или 9000. Bind только на `127.0.0.1` исключает случайный доступ из LAN.

## 2. Целевая Compose-конфигурация

Планируемая форма:

```yaml
services:
  tuner:
    build: .
    restart: unless-stopped
    ports:
      - "127.0.0.1:${APP_PORT:-43127}:43127"
    environment:
      TUNER_BIND_HOST: "0.0.0.0"
      TUNER_PORT: "43127"
      TUNER_PUBLIC_PORT: "${APP_PORT:-43127}"
      TUNER_DATA_DIR: "/data"
      TZ: "Europe/Warsaw"
    volumes:
      - tuner-data:/data
    healthcheck:
      test: ["CMD", "python", "-m", "app.healthcheck"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

volumes:
  tuner-data:
```

`0.0.0.0` внутри контейнера не делает сервис публичным: host-side mapping остаётся loopback-only. Default использует named volume, а не `./data` bind mount, чтобы fixed non-root user мог писать на первом запуске без host-specific `chown`. Такое начальное заполнение пустого volume подготовленным содержимым mount path — документированное [поведение Docker volumes](https://docs.docker.com/engine/storage/volumes/).

## 3. Dockerfile

Multi-stage:

1. `frontend-build`: Node 20 LTS, clean install по lockfile, typecheck, tests, `vite build`.
2. `backend-build`: установить Python dependencies в virtual environment/wheel layer.
3. `runtime`: slim Python image, non-root user, frontend dist и backend package, без Node/npm/build tools.

Runtime image:

- работает от фиксированных UID/GID `10001:10001` без root;
- содержит только необходимые CA certificates и timezone data;
- образ до `USER 10001:10001` создаёт `/data`, `/data/secrets`, `/data/backups` с владельцем `10001:10001` и режимами `0700`; при первом mount named volume сохраняет подготовленные ownership/permissions;
- выполняет migration command перед Uvicorn через безопасный entrypoint;
- использует один Uvicorn worker.

## 4. Планируемые команды

```bash
# Сборка и запуск
docker compose up --build -d

# Проверка
curl --fail http://127.0.0.1:43127/health/ready

# Логи без follow
docker compose logs --tail=200 tuner

# Безопасно импортировать Google client JSON; CLI пишет файл как UID 10001 с mode 0600
docker compose exec -T tuner python -m app.cli credentials import - < client_secret.json

# OAuth device flow
docker compose exec tuner python -m app.cli auth

# Ручной backup
docker compose exec tuner python -m app.cli backup

# Скопировать уже проверенный backup из volume на host
docker compose cp tuner:/data/backups/2026-08-01T12-00-00Z ./backups/2026-08-01T12-00-00Z

# Остановка без удаления данных
docker compose down
```

`docker compose down -v` удалит named volume со всей БД, OAuth и backup-копиями и поэтому запрещён как обычная команда. Full reset выполняется отдельной CLI-процедурой после внешней проверенной копии и точного подтверждения volume name.

## 5. Конфигурация

| Переменная | Default | Назначение |
| --- | --- | --- |
| `APP_PORT` | `43127` | host port Compose substitution |
| `TUNER_BIND_HOST` | `0.0.0.0` | listen address только внутри container; host mapping остаётся loopback |
| `TUNER_PORT` | `43127` | container listen port |
| `TUNER_PUBLIC_PORT` | `${APP_PORT:-43127}` | разрешённый port в browser Host/Origin и IFrame origin |
| `TUNER_DATA_DIR` | `/data` | БД, backups, secrets |
| `TZ` | `Europe/Warsaw` | отображение локального времени; БД всё равно UTC |
| `TUNER_LOG_LEVEL` | `INFO` | уровень логов |
| `TUNER_AUTO_PUBLISH` | `false` до onboarding | автопубликация после явного enable |
| `TUNER_RAW_EVENT_RETENTION_DAYS` | `180` | retention |

Runtime identity не настраивается environment variables: UID/GID `10001:10001` являются частью image contract и проверяются container test. OAuth client и token не передаются environment variables. Они читаются из файлов `/data/secrets/client.json` и `/data/secrets/oauth.json`.

Bind mount допускается только как явный advanced override. До запуска владелец host directory должен подготовить каталог для UID/GID `10001:10001` и проверить запись одноразовым container test; Tuner не запускает root-entrypoint и не делает автоматический recursive `chown` пользовательского пути.

## 6. Порядок первого запуска

1. Создать `.env` только при необходимости изменить `APP_PORT`.
2. `docker compose up --build -d`.
3. Открыть `/settings`; readiness должен быть green, YouTube status — not connected.
4. Импортировать Google client credentials через `credentials import -`; helper проверяет JSON и записывает его внутри named volume с mode `0600`.
5. Выполнить `python -m app.cli auth` и device flow.
6. Выполнить read-only initial sync.
7. Просмотреть три managed playlist plan и только затем создать их.
8. Autopublish по умолчанию остаётся выключен до успешного test publish/verify.

## 7. Health и restart

- Liveness не вызывает YouTube/Google.
- Readiness проверяет открытие БД, schema version и writable `/data`; внешний сервис не блокирует readiness.
- После restart scheduler освобождает истёкшие leases. CREATING/UNVERIFIED setup сначала reconciles по instance marker; publish в WRITING/VERIFYING сначала делает fresh remote read и затем становится COMPLETE, PARTIAL с continuation на следующее 24-часовое окно либо FAILED. Ни create, ни add/move слепо не повторяются.
- Compose `restart: unless-stopped` подходит для личного always-on режима.
- App startup не запускает полный sync немедленно, если последний успешный sync ещё в TTL.

## 8. Backup

Перед backup:

1. открыть SQLite online backup API либо выполнить `VACUUM INTO` в согласованный файл внутри named volume;
2. скопировать secret files отдельно с правами `0600`;
3. создать manifest с app/schema version, timestamp и SHA-256;
4. сохранить в `/data/backups/YYYY-MM-DDTHH-mm-ssZ/`;
5. проверить открытие backup database командой integrity check.

Автоматические daily backups хранятся 14 дней. Playlist snapshots внутри БД не заменяют backup всей БД.

Копия считается внешней только после `docker compose cp` в host directory и повторной проверки manifest/checksum на host. Backup, лежащий в том же named volume, не защищает от удаления volume.

## 9. Восстановление

- Остановить Compose без `-v`.
- Проверить checksum и SQLite integrity внешней backup-копии.
- Запустить одноразовый restore CLI в том же image: он внутри named volume перемещает текущую `/data/tuner.db` в timestamped quarantine, импортирует backup и выставляет owner/mode `10001:10001`/`0600`.
- Запустить основной container и применить только forward migrations.
- Проверить readiness, account identity и managed playlist manifest до включения jobs.

OAuth можно не восстанавливать: безопаснее повторно пройти connect, если происхождение backup неочевидно.

## 10. Обновление

1. Создать и проверить backup.
2. Собрать новый image с lockfiles.
3. Выполнить unit/contract tests.
4. Запустить миграцию на копии БД.
5. Обновить контейнер.
6. Проверить health, read-only sync и player smoke.
7. Оставить auto-publish выключенным до smoke test managed test playlist при изменении интеграционного слоя.

Откат image допускается только если новая миграция backward-compatible; иначе восстанавливается проверенный pre-upgrade backup.

## 11. Изменение порта

В `.env`:

```dotenv
APP_PORT=43128
```

После изменения обновляется разрешённый `origin` для IFrame Player, а UI/API продолжают использовать same-origin относительные URL. Перед выбором порт проверяется через `lsof -nP -iTCP:<port> -sTCP:LISTEN`.
