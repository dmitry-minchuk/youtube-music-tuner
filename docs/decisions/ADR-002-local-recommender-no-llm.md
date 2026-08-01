# ADR-002: локальный рекомендатель без обязательной LLM

- Статус: Accepted
- Дата: 2026-08-01
- Область: recommendation/AI

## Контекст

Нужно выбрать между внешней LLM, локальной маленькой моделью и обычной ML-библиотекой. На старте есть каталог/граф рекомендаций и небольшой поток feedback одного человека; полного аудиосигнала и крупной размеченной выборки нет.

## Рассмотренные варианты

### Внешняя LLM

Плюсы: понимает свободный текст, может красиво объяснять. Минусы: история покидает компьютер, стоимость/latency, слабая пригодность для числового online ranking, нет устойчивой памяти без отдельного feature store.

### Локальная general-purpose LLM

Плюсы: приватность и текстовый интерфейс. Минусы: model server, память/образ, всё ещё не тот inductive bias для ранжирования; объяснение не гарантирует правильный выбор трека.

### Audio embedding model

Плюсы: реальная акустическая близость. Минусы: нужен законный доступ к audio, большая вычислительная/операционная цена; ytmusicapi не является источником аудиофайлов для анализа.

### Классический локальный recommender/contextual bandit

Плюсы: учится на малом online feedback, миллисекунды CPU, прозрачен, воспроизводим, легко управляет exploration. Минусы: нужны аккуратные признаки и safeguards; нет магического понимания текста/аудио.

## Решение

Использовать гибрид:

1. candidate generation из кешированных related/radio/mood источников YouTube Music;
2. зафиксированный rule-based ranker для чистого baseline из 100 сессий;
3. локальный LinUCB/contextual bandit на NumPy в SHADOW после bootstrap 40 и serving только после 100 сессий/safety gates;
4. deterministic diversity/fatigue reranker;
5. temperature управляет quota и uncertainty bonus.

Serving ownership хранится явно: BASELINE/SHADOW обслуживает `rule-score-v1` с nullable shadow model, ACTIVE — конкретный LinUCB snapshot. Для общих playlist gates rule score отображается в `quality_expected=2*rule_score-1`, а ACTIVE использует LinUCB exploitation `theta^T x`; uncertainty/exploration не участвует в quality floor.

Никакая LLM не является runtime dependency и не получает telemetry.

## Последствия

- Docker остаётся лёгким и запускается без GPU/model download.
- Модель можно объяснить реальными reason codes.
- Raw telemetry остаётся локальной.
- Улучшение зависит от качества событий и candidate pool, а не от размера language model.
- Позже можно добавить маленькие metadata embeddings или optional text-to-mood parser как enrichment, не меняя core ranker.

## Условия пересмотра

- после 500+ квалифицированных сессий offline/online метрики показывают plateau;
- candidate diversity недостаточна из-за слабых metadata features;
- появляется законный и стабильный источник audio features;
- пользователь явно хочет natural-language mood input и согласует локальный model runtime.
