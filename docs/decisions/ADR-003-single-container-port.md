# ADR-003: один контейнер и порт 43127

- Статус: Accepted
- Дата: 2026-08-01
- Область: deployment

## Контекст

Проект личный, должен просто запускаться в Docker и предоставлять собственный web-player UI. Микросервисы, Kubernetes, Redis и отдельный model server не дают пользы одному пользователю, но увеличивают риск конфликтов и обслуживания.

Распространённые dev-порты часто заняты другими проектами. Нужен высокий, запоминаемый, но не знаменитый порт.

## Решение

- Собрать React frontend в multi-stage Dockerfile.
- Раздавать static bundle и `/api` одним FastAPI runtime-container.
- Использовать один Uvicorn worker, SQLite WAL, in-process persistent jobs и named volume `tuner-data:/data`.
- Запускать runtime с фиксированными UID/GID `10001:10001`; `/data` подготавливается с тем же owner в image до первого mount.
- Слушать внутри контейнера `43127`.
- Публиковать по умолчанию `127.0.0.1:43127:43127`.
- Разрешить override host port через `APP_PORT`.

На момент выбора 2026-08-01 listener на TCP 43127 на машине отсутствовал.

## Последствия

Плюсы: один build/run, same-origin UI/API, простые backups, нет межсервисной сети, минимальные ресурсы.

Минусы: один процесс объединяет HTTP и scheduler; долгие jobs должны быть короткими/асинхронными, а тяжёлое обучение в будущем потребует worker. Масштабирование на несколько пользователей не поддерживается.

## Guardrails

- job leases и idempotency обязательны даже при одном worker;
- внешний bind нельзя менять на LAN без нового security ADR;
- health/readiness не зависят от YouTube availability;
- player `origin` строится из фактического browser origin, поэтому смена `APP_PORT` поддерживается.
