# Изученные источники

Дата проверки: 2026-08-01.

## ytmusicapi

- [GitHub repository](https://github.com/sigma67/ytmusicapi) — позиционирование unofficial API, возможности, activity.
- [PyPI project](https://pypi.org/project/ytmusicapi/) — версия `1.12.1`, дата релиза 2026-06-05, Python requirement и release history.
- [Stable documentation 1.12.1](https://ytmusicapi.readthedocs.io/en/stable/) — актуальная пользовательская документация.
- [Setup](https://ytmusicapi.readthedocs.io/en/stable/setup/index.html) — установка и варианты auth.
- [OAuth authentication](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html) — собственный client ID/secret, тип `TVs and Limited Input devices`, `oauth.json`.
- [API reference](https://ytmusicapi.readthedocs.io/en/stable/reference/index.html) — library, history, watch/radio, moods и playlists methods.
- [Playlist reference 1.12.1](https://ytmusicapi.readthedocs.io/en/stable/reference/playlists.html) — initial `video_ids`, item identity `setVideoId` и `edit_playlist(moveItem=...)` для порядка.
- [Releases](https://github.com/sigma67/ytmusicapi/releases) — features/fixes и contributors.
- [Последние commits через GitHub API](https://api.github.com/repos/sigma67/ytmusicapi/commits?per_page=5) — live-проверка активности `main`.
- [Latest development documentation](https://ytmusicapi.readthedocs.io/en/latest/) — продолжающаяся dev-версия после stable release.

Проверка GitHub API 2026-08-01 показала свежие commits в `main` от 2026-07-25, включая parser fix, test parallelization и обновление lint/type-check инфраструктуры. Это operational evidence для решения, но lockfile всё равно фиксирует release, а не commit из `main`.

## YouTube

- [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference) — создание/управление player, события, `origin`, минимальный размер viewport.
- [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) — policy boundary, запрет undocumented API и background player вне просматриваемой страницы/вкладки/экрана.
- [Required Minimum Functionality](https://developers.google.com/youtube/terms/required-minimum-functionality) — требования к embedded playback/client behavior.

## Рекомендательные системы

Изучено 2026-08-01 при переработке candidate generation (ADR-004).

- [A Comprehensive Survey on Retrieval Methods in Recommender Systems](https://arxiv.org/pdf/2308.01204) — трёхступенчатая воронка candidate generation → ranking → re-ranking и роль retrieval-стадии.
- [Result Diversification in Search and Recommendation: A Survey](https://arxiv.org/pdf/2212.14464) — MMR против DPP, миопичность жадного отбора.
- [Determinantal Point Processes для разнообразия](https://medium.com/data-science-collective/diversity-in-recommendations-determinantal-point-processes-dpp-2427bf1b6324) — оконный DPP, размеры окна 6–12 в продуктовых лентах.
- [Item2Vec](https://www.emergentmind.com/papers/1603.04259) — эмбеддинги из совместной встречаемости, без анализа аудио.
- [Item-Graph2vec](https://arxiv.org/pdf/2310.14215) — граф ко-встречаемости как развитие Item2Vec.
- [A Random Walk Model for Item Recommendation](https://arxiv.org/pdf/1310.7957) и [Boosting Item-based CF via Nearly Uncoupled Random Walks](https://arxiv.org/pdf/1909.03579) — обход графа похожести как генерация кандидатов.
- [Spotify Sequential Skip Prediction Challenge](https://www.aicrowd.com/challenges/spotify-sequential-skip-prediction-challenge) и [RNN-подход](https://arxiv.org/pdf/1904.10273) — пропуск как первоклассный сигнал.
- [Calibrated Recommendations with Contextual Bandits](https://research.atspotify.com/2025/9/calibrated-recommendations-with-contextual-bandits-on-spotify-homepage) — квота состава как калибровка, а не тай-брейкер.
- [Contextual and Sequential User Embeddings (CoSeRNN)](https://research.atspotify.com/2021/04/contextual-and-sequential-user-embeddings-for-music-recommendation) — предсказание предпочтений на старте сессии.
- [Efficient Exploration and Exploitation for Sequential Music Recommendation](https://dl.acm.org/doi/10.1145/3625827) — почему чисто случайное исследование сходится медленно.
- [Интервью команды «Моей волны»](https://the-flow.ru/features/yandeks-moya-volna-intervyu) — три класса алгоритмов, подстройка на каждом треке, взвешивание сигналов по значимости.

## Docker

- [Docker volumes](https://docs.docker.com/engine/storage/volumes/) — lifecycle named volumes, default copy/pre-population пустого volume и backup/restore guidance.

## Как применять источники

- `ytmusicapi` docs определяют реальный поддерживаемый интерфейс adapter.
- Официальная YouTube документация имеет приоритет для playback и UI constraints.
- Ни один источник не гарантирует стабильность внутренних YouTube Music endpoints; поэтому caching, adapter tests, conservative call budget и rollback обязательны.
- Перед началом реализации и каждым dependency upgrade нужно повторно проверить stable version, setup/OAuth changes и repository activity.
