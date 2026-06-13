# Daily Song — tasks

Реализация спеки [`requirements.md`](./requirements.md) по дизайну [`design.md`](./design.md).

Маркеры статуса (см. `.kiro/steering/workflow.md`):
`[ ]` — не начато · `[~]` — в открытом PR · `[x]` — смерджено в основную ветку · `[!]` — заблокировано

## Прогресс по фазам

| Фаза | Что делает | Статус | Done | In PR | Total |
|------|-----------|--------|------|-------|-------|
| 0 | Спека (этот файл + requirements + design) | `[x]` | 1 | 0 | 1 |
| A | Suno API настройка через бота (sunoapi.org) — независимая от 1–6 | `[x]` | 7 | 0 | 7 |
| 1 | Capture pipeline: миграция `chat_messages`, хэндлер-логгер, конфиг-фикс | `[x]` | 5 | 0 | 5 |
| B | Music storage UI: `/musiclist` + `/musicmenu`, песни хранятся бессрочно | `[x]` | 4 | 0 | 4 |
| C | Observability + unified `/musicmenu` + simplified OpenRouter + target duration | `[x]` | 7 | 0 | 7 |
| D | Song-from-chat MVP: `song_pipeline.py` + `/song_now` + кнопка в /musicmenu | `[x]` | 5 | 0 | 5 |
| E | Scheduled daily song: миграция `chats.song_*`, scheduler-job, headless pipeline, UI расписания | `[~]` | 0 | 4 | 4 |
| 2 | LLM-абстракция: OpenRouter-клиент, таблица `llm_models`, рантайм-управление через `/menu` | `⛔ superseded (C)` | 1 | 0 | 6 |
| 3 | Summarizer + songwriter: map-reduce, JSON-парсинг с ретраями, dry-run `/song_test` | `⛔ superseded (D)` | 0 | 0 | 4 |
| 4 | Song-провайдер + оркестратор: миграция `daily_songs`, SunoApiOrgProvider+LyricsOnly, `daily_song.py`, `/song_now`, scheduler-job, постинг в чат | `[~]` | 1 | 6 | 7 |
| 5 | Полировка: `/song_stats`, `/song_purge`, alert при первом включении, sweep `stale_on_restart` | `[~]` | 1 | 3 | 4 |
| F | Dedup по дню + LyricsOnly fallback + обложка mp3 + pytest smoke-сьют | `[~]` | 0 | 4 | 4 |
| H | Публичная `/music <текст> [стиль]`: LLM улучшает рифмы + Suno, песня в чат | `[~]` | 0 | 3 | 3 |
| 6 | Опционально: тесты-смоук, retention-cron, обложка mp3 | `[ ]` | 0 | 0 | 3 |

**Итого**: 32 / 20 / 64

> Из 61 задачи **superseded MVP-архитектурой** (Фазы C/D): 9 (вся Фаза 2 кроме
> 2.4 + вся Фаза 3). **N/A**: 6.2 (retention уже 2 дня). Остальное реализовано
> или в открытых PR. Фаза 4 закрыта полностью: scheduler+постинг (E),
> `/song_now` (D), ledger `daily_songs` + provider-абстракция + оркестратор
> `daily_song.py` + sweep `stale_on_restart` (этот PR). 6.1/6.3 — в Фазе F.
> Функционально фича готова end-to-end: capture → OpenRouter → Suno → постинг,
> ручной (`/song_now`) и по расписанию с дедупом, обложкой и текстовым
> fallback при отказе Suno.

## Открытые PR

| PR | Ветка | Фаза | Описание |
|----|-------|------|----------|
| [#30](https://github.com/pavlodrab/ideabottg/pull/30) | `feat/scheduled-daily-song` | E | автоматическая «Песня дня» по расписанию: per-chat opt-in + cron-job поверх `song_pipeline`, UI расписания в per-chat `/musicmenu` |
| [#31](https://github.com/pavlodrab/ideabottg/pull/31) | `feat/song-stats-purge` | 5 | `/song_stats` + `/song_purge` (OWNER, с подтверждением); стек поверх PR #30 |
| [#32](https://github.com/pavlodrab/ideabottg/pull/32) | `feat/song-dedup-fallback-tests` | F · 4 · 6 | dedup по дню + LyricsOnly fallback + обложка mp3 + pytest smoke-сьют; стек поверх PR #31 |
| [#33](https://github.com/pavlodrab/ideabottg/pull/33) | `feat/daily-songs-ledger` | 4 · 5.3 | ledger `daily_songs` (миграция 0009 + модель) + provider-абстракция `song_provider.py` + оркестратор `daily_song.py` + sweep `stale_on_restart`; стек поверх PR #32 |
| _TBD_ | `feat/public-music-command` | H | публичная `/music <текст> [стиль]` — LLM причёсывает рифмы + Suno, mp3 в чат; стек поверх PR #33 |

---

## Фаза 0 — Спека

- [x] **0.1** Написать requirements / design / tasks + steering `workflow.md` _(PR [#25](https://github.com/pavlodrab/ideabottg/pull/25) — merged)_

---

## Фаза A — Suno API настройка через бота (sunoapi.org)

Параллельная независимая фаза: даёт юзеру возможность задать API-ключ
sunoapi.org и сделать тестовую генерацию **до** того, как сам daily-song
пайплайн будет готов. По задаче от владельца («dobav suno api chtobi
vse cherez bota nastroit»). Никаких env-переменных для Suno — всё через
`/suno` в боте, ключ хранится в существующей таблице `settings`.

- [x] **A.1** Фикс пре-существующего бага `app/config.py` — `Field` использовался без импорта, бот не стартовал. Однострочный фикс: `from pydantic import Field`. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **A.2** Сервисы:
  - `app/services/settings.py` — generic k/v helpers (`get_setting` / `set_setting` / `delete_setting`) поверх существующей таблицы `settings`. Полезно и для будущей Phase 2 (`llm.active_*` ключи), и для `suno.*`.
  - `app/services/suno.py` — `SunoApiOrgClient` (httpx) c `get_credits` / `generate_music` / `get_task`, dataclass `TaskSnapshot`, helpers `mask_key` / `get_api_key` / `set_api_key` / `clear_api_key` / `get_model` / `set_model` / `get_callback_url`. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **A.3** UI:
  - `app/keyboards/suno.py` — главное меню Suno, выбор модели, бэк-кнопка, подтверждение удаления ключа.
  - `app/handlers/suno_admin.py` — команды `/suno`, `/suno_credits`, `/suno_status <task_id>`; callback'и `suno:home` / `suno:set_key` / `suno:remove_key{,_yes}` / `suno:credits` / `suno:model_open` / `suno:model_set:<slug>` / `suno:gen_open`; FSM-стейты `SunoApiKeyEditing`, `SunoTestPrompt` в `app/states.py`.
  - Кнопка «🎵 Suno API» в `home_keyboard`.
  - Регистрация роутера в `app/handlers/__init__.py` после `admin_users` и до `chats` (DM-only хэндлеры). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **A.4** Тестовая генерация:
  - При вводе ключа сначала валидируем `GET /api/v1/generate/credit` — если не 200, не сохраняем.
  - При тестовой генерации вызываем `POST /api/v1/generate` в режиме `customMode=false, instrumental=false` (только prompt, lyrics auto), и стартуем фоновый поллер через `asyncio.create_task` с интервалом 15 сек, таймаутом 360 сек. Когда задача в `SUCCESS` — отправляем mp3 как `send_audio(audio_url)`, на фейл/таймаут — редактируем плейсхолдер.
  - Сообщение с ключом удаляется из истории чата сразу после получения, чтобы ключ не светился. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **A.5** Спека / зависимости / `.env.example`:
  - `httpx==0.27.2` в `requirements.txt`.
  - `.env.example` — секция отмечает, что для Suno env не нужен (всё через `/suno`).
  - Этот файл (`tasks.md`) и `design.md` обновлены: §3.6 описывает `SunoApiOrgProvider` как готовый к интеграции в Phase 4, §7 разделён на 7.1 (Suno в БД) и 7.2 (daily-song env). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **A.6** Хардинг под реальные доки sunoapi.org:
  - `TaskSnapshot.from_response` теперь принимает обе формы списка треков из доков — OpenAPI `response.sunoData[]` (camelCase) И Quickstart `response.data[]` (snake_case).
  - `get_credits` сначала пробует `/api/v1/generate/credit` (OpenAPI), на 404 падает на `/api/v1/get-credits` (Quickstart-сэмпл) — официальная дока противоречит сама себе.
  - `STATUS_FAILED` добавлен в `TERMINAL_STATUSES` (упомянут в Quickstart-прозе, отсутствует в OpenAPI-enum). Неизвестные статусы (`GENERATING`, etc.) трактуем как нетерминальные — лучше лишний раз популить, чем зависнуть.
  - `TaskSnapshot.error_message` парсит `data.errorMessage` (есть в OpenAPI-схеме) — теперь юзеру в фейле видна причина из API, а не только статус-код.
  - `SunoApiError.humanized()` — гуманайзер кодов ошибок Suno (401/413/429/430/455/etc.) на русский. Используется во всех местах вывода ошибок (валидация ключа, баланс, генерация, статус). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
  - В success-сообщении тестовой генерации появилась подсказка про 15-дневный retention файлов на серверах Suno.
- [x] **A.7** Архив сгенерированных песен + захват Telegram `file_id`:
  - Новая таблица `songs` (миграция `0006`): id, chat_id, suno_task_id, suno_audio_id, title, style, model, prompt, lyrics, audio_url, stream_url, image_url, duration, **tg_audio_file_id**, requested_by, status, created_at. Songs хранятся бессрочно — retention их не трогает.
  - Сервис `app/services/songs.py`: `upsert_song` (идемпотентен по `suno_task_id`, безопасен для polling-callbacks `text` → `first` → `complete`), `set_tg_file_id`, `list_songs_for_chat`, `list_songs_for_user(is_admin)`, `get_song`.
  - В `_watch_task` (Suno test-gen) после `SUCCESS` сразу пишется `Song`-row, после успешного `send_audio` сохраняется Telegram `file_id` — это значит, что после первого проигрывания в боте песню можно бесконечно отдавать через Telegram даже после Suno's 15-day URL retention. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_

**Definition of done фазы A**: владелец задаёт ключ через `/suno → 🔑 Задать API-ключ`, видит баланс кредитов, выбирает модель, нажимает «🧪 Тестовая генерация», вводит prompt → через 2–3 минуты бот в личке присылает mp3.

**Что НЕ делает фаза A** (это уже Phase 4): запись `chat_messages`, оркестратор `daily_song.py`, постинг в чат, scheduler-job. Фаза A только готовит инфраструктуру и UI; обёртка `SunoApiOrgProvider` поверх `SunoApiOrgClient` появится в Phase 4.

---

## Фаза 1 — Capture pipeline

Цель: к концу фазы бот **умеет писать в БД** все сообщения опт-инутого чата. Без LLM, без Suno. Без этого ни одна следующая фаза не имеет смысла.

- [ ] **1.1** Alembic-миграция `20260613_0006_daily_song_capture.py`:
  - `ALTER TABLE chats ADD COLUMN song_enabled BOOLEAN NOT NULL DEFAULT FALSE`
  - `ALTER TABLE chats ADD COLUMN song_started_at TIMESTAMPTZ NULL`
  - `ALTER TABLE chats ADD COLUMN last_song_sent_at TIMESTAMPTZ NULL`
  - `CREATE TABLE chat_messages (...)` + индексы (см. design §2.2)
  - downgrade пишем зеркально, без жалости.
- [ ] **1.2** Модель `ChatMessage` в `app/models.py` + 3 новых поля в `Chat`.
- [ ] **1.3** Сервис `app/services/chat_messages.py`: `record_message`, `fetch_window`, `purge_chat_history`. Игнор: `/...`, боты, пустой текст, sysmsg. Idempotency на `(chat_id, tg_message_id)` через `INSERT ... ON CONFLICT DO UPDATE` (для edits).
- [ ] **1.4** Хэндлер `app/handlers/chat_logger.py`. Регистрируется в `register_handlers` **после** `ideas`-роутера. Не возвращает «consumed» — даёт сообщению идти дальше.
- [ ] **1.5** Конфиг-расширение в `app/config.py`:
  - Поправить существующий баг `Field(...)` без импорта (риск R4): либо добавить `from pydantic import Field`, либо переписать `quiet_hours_*` без `Field`. Сначала проверить, падает ли реально на старте — `python -c "from app.config import settings"`.
  - Добавить новые поля: `song_enabled, song_tz, song_cron, song_min_messages, song_max_messages, song_message_max_len`. Без `Field`-обёртки, чтобы не повторять чужой стиль.
  - Обновить `.env.example` секцией `# --- Daily song feature ---`.

**Definition of done фазы 1**: `python -c "from app.handlers.chat_logger import router"` импортируется; миграция `alembic upgrade head` проходит локально (или хотя бы `alembic check`); `record_message` тестируется через `python -c` со замоканным сообщением.

---

## Фаза 2 — LLM-абстракция и рантайм-управление моделями

> **⛔️ SUPERSEDED Фазой C (PR #28).** MVP пошёл по упрощённому пути: одна
> активная модель OpenRouter + один system prompt в таблице `settings`
> (`llm.*`), вместо таблицы `llm_models` с per-role активацией. См.
> design.md §3.2 «MVP simplification». `OpenRouterClient` (2.4) реализован
> в `app/services/llm.py`. Задачи 2.1–2.3, 2.5–2.6 (CRUD моделей)
> **не реализуются** — остаются post-MVP расширением, если упрёмся в
> качество одной модели. Из burndown исключены (см. сноску у «Итого»).

- [ ] **2.1** ~~Alembic-миграция `llm_models`~~ — superseded (settings-based).
- [ ] **2.2** ~~Модель `LlmModel`~~ — superseded.
- [ ] **2.3** ~~Сервис `llm_models.py` (CRUD + активация ролей)~~ — superseded.
- [x] **2.4** Сервис `app/services/llm.py`: `OpenRouterClient` поверх `httpx` + `httpx` в `requirements.txt` — реализовано в Фазе C. _(PR [#28](https://github.com/pavlodrab/ideabottg/pull/28) — merged)_
- [ ] **2.5** ~~FSM `LlmModelAdd` / `LlmModelEditPrompt`~~ — superseded (есть `LlmApiKeyEditing` / `LlmModelEditing` / `LlmSystemPromptEditing` из Фазы C).
- [ ] **2.6** ~~UI «🤖 LLM-модели» (CRUD моделей)~~ — superseded меню `🤖 OpenRouter` из Фазы C.

**Definition of done фазы 2**: ~~CRUD моделей через меню~~ — заменено на «один ключ + одна модель + один prompt» через `/musicmenu → 🤖 OpenRouter` (Фаза C).

---

## Фаза 3 — Summarizer + songwriter (без Suno)

> **⛔️ SUPERSEDED Фазой D (PR #29).** Вместо отдельных map-reduce
> `song_summarizer.py` + `song_writer.py` MVP делает summarize+songwrite
> **одним** LLM-вызовом в `app/services/song_pipeline.py::llm_make_song_draft`
> (с JSON-ретраями и tolerant-парсингом). Dry-run `/song_test` заменён
> рабочими `/song_now` + кнопкой в `/musicmenu` (Фаза D). Отдельные
> модули не реализуются — из burndown исключены.

- [ ] **3.1** ~~`song_summarizer.py` (map-reduce)~~ — superseded `song_pipeline.llm_make_song_draft`.
- [ ] **3.2** ~~`song_writer.py` (`digest_to_song`)~~ — superseded (тот же единый вызов; songwriter system prompt — в `llm.DEFAULT_SONGWRITER_SYSTEM_PROMPT`).
- [ ] **3.3** ~~`/song_test` dry-run~~ — superseded `/song_now` + `mm:gen_*` (Фаза D, PR #29).
- [ ] **3.4** ~~Smoke `/song_test`~~ — superseded (минимум сообщений проверяется в `start_song_generation` → `too_few_messages`).

**Definition of done фазы 3**: ~~`/song_test` возвращает SongDraft~~ — заменено на `/song_now <chat_id>` → реальная песня в чат (Фаза D).

---

## Фаза 4 — Song-провайдер + оркестратор + scheduler + постинг

> **Реализовано.** 4.5 — Фаза E (PR #30). 4.6 — Фаза D (PR #29). 4.7 заменён
> UI расписания (Фаза E). 4.1–4.4 — **этот PR** (`feat/daily-songs-ledger`):
> таблица `daily_songs`, модель `DailySong`, `song_provider.py` (Protocol +
> SunoApiOrg + LyricsOnly + factory; self-hosted — явный TODO в factory),
> оркестратор `daily_song.py` с ledger-дедупом и LyricsOnly-fallback.

Цель: полный пайплайн работает; cron каждый день постит mp3 в чат.

- [~] **4.1** Alembic-миграция `0009_daily_songs`: таблица `daily_songs` (per-(chat, date) ledger, unique `(chat_id, date_local)`). _(PR [#33](https://github.com/pavlodrab/ideabottg/pull/33))_
- [~] **4.2** Модель `DailySong` в `app/models.py`. _(PR [#33](https://github.com/pavlodrab/ideabottg/pull/33))_
- [~] **4.3** `app/services/song_provider.py`: `SongProvider` Protocol + `SunoApiOrgProvider` (обёртка `SunoApiOrgClient`) + `LyricsOnlyProvider` + фабрика `get_song_provider` (ключ `suno.provider`, default `sunoapi_org`). `self_hosted` — явный `SongProviderError` (не подключён). _(PR [#33](https://github.com/pavlodrab/ideabottg/pull/33))_
- [~] **4.4** `app/services/daily_song.py`: оркестратор `run_daily_song_for_chat` — ledger queued→generating→done/skipped/failed, дедуп по дню через unique-индекс, постинг mp3+обложка+lyrics, fallback на `LyricsOnlyProvider`/текст при фейле/таймауте Suno. _(PR [#33](https://github.com/pavlodrab/ideabottg/pull/33))_
- [x] **4.5** Scheduler job-тип `song:{chat_id}`, `_schedule_song`, `_run_song`, регистрация в `start()`/`sync_chat()` — Фаза E. _(PR [#30](https://github.com/pavlodrab/ideabottg/pull/30))_
- [x] **4.6** `/song_now <chat_id>` — Фаза D. _(PR [#29](https://github.com/pavlodrab/ideabottg/pull/29) — merged)_
- [x] **4.7** Toggle «Песня дня» — реализован как UI расписания «📅 Расписание песни дня» в per-chat `/musicmenu` (Фаза E). _(PR [#30](https://github.com/pavlodrab/ideabottg/pull/30))_

**Definition of done фазы 4**: `/song_now` → mp3 в чате; по cron — авто-постинг с дедупом и текстовым fallback. ✔ (`_run_song` → `daily_song.run_daily_song_for_chat`).

---

## Фаза 5 — Полировка

> **5.1 / 5.2 — в [PR #31](https://github.com/pavlodrab/ideabottg/pull/31) (`feat/song-stats-purge`).** 5.4 уже закрыт ранее (`mask_key` в #26/#28). 5.3 неприменим в текущем MVP.

- [~] **5.1** `/song_stats` (DM, admin) — `songs.song_stats(days=30)`: всего песен, за 30 дней, топ-10 по чатам, распределение не-success статусов.
- [~] **5.2** `/song_purge <chat_id>` (только OWNER) — `chat_messages.purge_chat_history`, inline-confirm с числом сообщений. Песни не трогаются (N1.3).
- [~] **5.3** Sweep при старте (F8.3): `daily_songs` со статусом `queued`/`generating` старше 24ч → `failed, error="stale_on_restart"`. Реализовано: `daily_songs.sweep_stale`, вызывается в `IdeaScheduler.start()`. _(PR [#33](https://github.com/pavlodrab/ideabottg/pull/33))_
- [x] **5.4** Маскирование API-ключей в логах — закрыто: `mask_key` в `app/services/suno.py` и `app/services/llm.py` используется во всех лог-строках с ключом; сырой ключ нигде не логируется. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26) — merged, [#28](https://github.com/pavlodrab/ideabottg/pull/28) — merged)_

---

## Фаза 6 — Опционально (post-MVP)

> **6.1 и 6.3 реализованы в Фазе F** (см. ниже). 6.2 — N/A.

- [x] **6.1** Smoke-тесты — реализованы в Фазе F (F.4): pytest-сьют на pure helpers + DB (dedup/stats/purge). Адаптировано под фактическую архитектуру (`song_pipeline`, а не `summarize_day`/`LyricsOnlyProvider`-классы).
- [ ] **6.2** ~~Retention-cron `chat_messages` старше 30 дней~~ — **N/A**: retention уже работает с окном 2 дня (`RETENTION_DAYS=2`, hourly job, PR #26). Второй cron с другим окном избыточен.
- [x] **6.3** Обложка — реализовано в Фазе F (F.3): `image_url` из Suno постится как photo перед mp3.

---

## Фаза F — Dedup + LyricsOnly fallback + обложка + тесты

> **Все задачи ниже — в [PR #32](https://github.com/pavlodrab/ideabottg/pull/32) (`feat/song-dedup-fallback-tests`), стек поверх PR #31.** После мерджа маркеры переключаются `[~]` → `[x]`.

Закрывает «хвост» Фазы 4 в lean-форме (без таблицы `daily_songs` и
provider-абстракции) + опциональную Фазу 6. Всё проверено реально:
`pytest` (23 теста зелёные), `alembic upgrade head` до 0008 на sqlite,
импорт 49 модулей + сборка диспатчера.

- [~] **F.1** Dedup по дню (интент 4.4): `songs.has_song_on_date(chat_id, day_start_utc, day_end_utc)`; в `run_scheduled_song_for_chat` день считается в TZ `settings.tz`, повторная генерация в тот же день (cron-misfire/coalesce, рестарт, ручной `/song_now`) пропускается.
- [~] **F.2** LyricsOnly fallback (F5.4 / интент 4.3): `_post_lyrics_only` + параметр `post_lyrics_on_failure` в `watch_suno_task`/`handle_terminal`. На таймаут/фейл Suno в scheduled-флоу в чат уходит текст песни. Ручные флоу не трогаются (админ видит ошибку в DM-плейсхолдере).
- [~] **F.3** Обложка mp3 (6.3): `snapshot.image_url` постится как `send_photo` перед `send_audio` (best-effort, не блокирует доставку mp3).
- [~] **F.4** Smoke-тесты (6.1): `tests/` + `pytest.ini` + `requirements-dev.txt`. `test_song_helpers.py` (build/trim/json-parse/draft/cron) и `test_songs_db.py` (`has_song_on_date`/`song_stats`/`purge_chat_history` на in-memory sqlite). 23 теста.

**Definition of done фазы F**:
1. `pytest` → 23 passed.
2. Scheduled-джоб не постит вторую песню в тот же день.
3. При недоступности Suno в авто-режиме чат получает текст песни, а не тишину.
4. У песни появляется обложка, если Suno её вернул.

---

## Фаза 1 — Capture pipeline

- [x] **1.1** Миграция `0006_chat_messages_and_songs`: новая таблица `chat_messages` (id, chat_id FK, tg_message_id, user_id, username, full_name, text, created_at) + индексы и UniqueConstraint(chat_id, tg_message_id) inline (sqlite-friendly). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **1.2** Модель `ChatMessage` в `app/models.py` + сервис `app/services/chat_messages.py` (`insert_message` с дедупом по unique-constraint, `count_messages`, `fetch_messages_since`, `delete_older_than`, `cutoff_for_retention`). RETENTION_DAYS=2, MAX_TEXT_LEN=2000. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **1.3** Capture middleware `app/middlewares/capture.py`: ловит текст и captions из group/supergroup, пропускает ботов, команды (`/`), не-text сообщения, не-зарегистрированные и paused чаты. Всегда swallow exceptions — никогда не блокирует основной handler-chain. Регистрируется ПОСЛЕ `DbSessionMiddleware` чтобы иметь `data["session"]`. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **1.4** Retention scheduler-job в `IdeaScheduler.start()`: cron `5 * * * *` (каждый час в xx:05), запускает `delete_older_than(cutoff_for_retention(2))`. Логирует число удалённых строк. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **1.5** Команда `/captured [chat_id]` в `app/handlers/music.py` — DM-only, admin-only диагностика: показывает 24h-window и total-в-окне-retention для каждого зарегистрированного чата (без аргумента) или для одного (с chat_id-аргументом). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_

---

## Фаза B (новая) — Music storage UI поверх Suno

Параллельная фаза, реализованная вместе с Phase 1: даёт юзерам видеть и
проигрывать сгенерированные песни прямо в Telegram, и админам настраивать
дефолтный стиль для каждого чата.

- [x] **B.1** `/musiclist` в `app/handlers/music.py` — открыто всем, без admin-gate. В групповом чате показывает песни этого чата; в DM показывает песни юзера (по `requested_by`), а админ видит весь cross-chat архив. По 5 на страницу, кнопки `▶️ N` отправляют mp3 в-place через `send_audio`. После первого `send_audio` Telegram `file_id` сохраняется в `Song.tg_audio_file_id` и используется для всех последующих воспроизведений (бессмертно). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **B.2** `/musicmenu` — admin-only, выбор дефолтного стиля для чата. В группе сразу открывает меню для текущего чата; в DM показывает chat-picker. 12 пресетов (Pop / Rock / Lo-fi / Folk / Synthwave / Hip-hop / Classical / Jazz / Electronic / Ambient / Indie / Metal) + Custom (FSM-ввод произвольного текста до 500 символов) + Reset. Сохраняется в `chats.song_style` (новая колонка). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **B.3** Keyboards `app/keyboards/music.py`: `music_list_keyboard` (play row + pagination), `music_menu_keyboard` (12 presets с ✅-маркером + Custom + Reset), `music_chat_picker_keyboard` (DM выбор чата), `music_style_back_keyboard`. Callback-namespace `music:*` (без коллизий с `suno:*` / `chat:*` / `prompt:*` / etc.). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **B.4** FSM `MusicCustomStyle.waiting_text` в `app/states.py` для свободного ввода кастомного стиля. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_

**Definition of done фазы B**: юзер в групповом чате пишет `/musiclist` → видит ленту песен, тапает `▶️ 1` → бот присылает mp3 в чат. Админ в той же группе пишет `/musicmenu` → выбирает «🌙 Lo-fi» → следующая Phase-4 daily-song будет генериться в этом стиле.

---

## Фаза C — Observability + unified `/musicmenu` + simplified OpenRouter + target duration

> **Все задачи ниже смерджены в [PR #28](https://github.com/pavlodrab/ideabottg/pull/28)** (`feat/openrouter-musicmenu-logs`) — маркеры `[x]`.

Параллельная фаза, реализованная вместе с Phase 1 / B. Цель — закрыть
владельцеву задачу «всё управление ботом в одной менюшке `/musicmenu`»,
и подготовить инфраструктуру для Phase 3+ (генерация песни на основе
чата): live-логи в боте, OpenRouter-клиент с одной моделью / system
prompt'ом, ручка длины песни 2–3 минуты.

Существенное архитектурное решение — для MVP мы **не** идём по
исходной Phase-2 с таблицей `llm_models` (CRUD моделей и per-role
активация). Вместо этого простые DB-настройки в существующей таблице
`settings`: `llm.api_key`, `llm.model`, `llm.system_prompt`,
`llm.referer`. См. обновлённый design.md §3.2 «MVP simplification».
Phase 2 (`llm_models`) остаётся как **post-MVP расширение**.

- [x] **C.1** `app/services/logs.py` — `RingBufferLogHandler` (deque, capacity 500), `install_ring_buffer_handler()`, `get_recent()`, словарь уровней `LEVEL_TOKENS`. Подключается из `app/main.py` сразу после `logging.basicConfig` — параллельно со stdout, не вместо него. Зависимостей не добавляет.
- [x] **C.2** `app/handlers/logs.py` — команда `/logs [level] [N]` (DM-only, admin-only), inline-клавиатура переключения уровня (Все / INFO+ / WARN+ / ERROR+), кнопка «📥 Скачать .txt». Если рендер >80 строк — отдаём документом, иначе `<pre>`-блоком. Кнопка «📜 Логи» добавлена в новое `/musicmenu`.
- [x] **C.3** Unified DM `/musicmenu` (admin home):
  - Новый `app/keyboards/musicmenu.py` (`musicmenu_home_keyboard`, `render_musicmenu_home_text`, `musicmenu_styles_keyboard`) — рендерит health-индикаторы 🟢/🔴 для Suno и OpenRouter API-ключей, текущую модель и target-duration прямо на главном экране.
  - Новый `app/handlers/musicmenu_admin.py` — обрабатывает `/musicmenu` в DM, callback'и `mm:home` / `mm:styles` / `mm:archive` + перехватывает legacy `home` callback.
  - `/musicmenu` в группе остаётся per-chat (style picker) — фильтр в `app/handlers/music.py::cmd_musicmenu` теперь только `group/supergroup`.
  - `/start` для админов и `/menu` показывают тот же unified экран (через `build_home_view`).
  - Старая `home_keyboard` в `app/keyboards/menus.py` остаётся как dead-code-fallback (без callsites), `_home_text` помечен как legacy.
- [x] **C.4** Simplified OpenRouter (без llm_models таблицы):
  - `app/services/llm.py` — `OpenRouterClient` (httpx) с `chat()` и `get_key_info()`, dataclass'ы `LlmKeyInfo` / `ChatResult` (с `parse_json`-helper'ом), `LlmApiError` с `humanized()`-маппингом кодов 401/402/404/408/413/429/5xx. DB-helpers `get/set/clear_api_key`, `get/set_model`, `get/set_system_prompt`, `get_referer`. Каталог `SUPPORTED_MODELS` с дефолтом `google/gemini-2.0-flash-exp:free` (бесплатный) и встроенным songwriter system prompt.
  - `app/keyboards/llm.py` — главное меню, picker модели, экран prompt'а, подтверждение удаления ключа.
  - `app/handlers/llm_admin.py` — команды `/llm`, callback'и `llm:home / set_key / remove_key{,_yes} / credits / model_open / model_set:<slug> / model_custom / prompt_open / prompt_edit / prompt_reset / test_open`. FSM-стейты `LlmApiKeyEditing`, `LlmModelEditing`, `LlmSystemPromptEditing`, `LlmTestPrompt` в `app/states.py`. Валидация ключа звонком в `/auth/key`. «🧪 Тестовый запрос» — отправляет user-prompt в активную модель с текущим system prompt и возвращает сырой ответ + статус JSON-парсинга.
- [x] **C.5** Suno target-duration:
  - `app/services/suno.py` — `KEY_TARGET_DURATION_SEC`, `DEFAULT_TARGET_DURATION_SEC=150`, `DURATION_PRESETS_SEC=(90,120,150,180,240)`, `MIN/MAX_TARGET_DURATION_SEC`, helpers `get/set_target_duration_sec`, `format_duration_label`, `format_duration_hint`, `append_duration_hint` (идемпотентен — не дублирует `[Length:` если уже есть в prompt).
  - `app/keyboards/suno.py::suno_duration_keyboard` — пресеты 1:30 / 2:00 / 2:30 / 3:00 / 4:00 + кастомный ввод.
  - `app/handlers/suno_admin.py` — кнопка «🎯 Длительность» в основном меню Suno + дублирование на главном `/musicmenu` экране, callback'и `suno:duration_open / duration_set:<sec> / duration_custom`, FSM `SunoDurationCustom.waiting_seconds`.
  - В `receive_test_prompt` (Suno test-gen) к user-промпту автоматически добавляется duration-hint через `append_duration_hint(...)` — Suno чаще попадает в 2-3 минуты вместо 4-минутного потолка V4.
- [x] **C.6** `chat_messages` retention sanity:
  - `MAX_TEXT_LEN` 2000 → 4096 (телеграм-cap), чтобы лонгриды не теряли хвост перед уходом в LLM-summarizer.
  - Новый helper `oldest_message_at(chat_id?)` — возвращает timestamp самой старой строки в `chat_messages`.
  - `/captured` в DM (как с аргументом `<chat_id>`, так и без) теперь показывает «Самое старое: YYYY-MM-DD HH:MM UTC» — наглядное подтверждение, что retention-job (час/раз с PR #26) реально работает и не даёт таблице вырасти бесконечно.
- [x] **C.7** Help-текст и навигация:
  - `/start` для админа сразу открывает unified `/musicmenu` экран вместо текстового списка команд.
  - `/help` обновлён под новый набор команд (`/musicmenu`, `/llm`, `/logs`).
  - Старые callbacks `home` со старых клавиатур (Suno, qh, chats list) теперь ведут на тот же unified экран (`mm:home` логика в musicmenu_admin перехватывает их).

**Definition of done фазы C**:
1. Владелец пишет `/musicmenu` в DM → видит единый экран с 🟢/🔴 индикаторами Suno и OpenRouter, кнопками для длительности, стилей чатов, логов.
2. Тапает «🤖 OpenRouter · 🔴 · ...» → попадает в OpenRouter-меню → задаёт ключ openrouter.ai → бот валидирует через `/auth/key`, показывает `usage`/`limit` → главный экран теперь 🟢.
3. Тапает «🧪 Тестовый запрос», отправляет «Сделай SongDraft про субботнее утро» → бот возвращает JSON в `<pre>` блоке + статус `✅ JSON parsed`.
4. Тапает «🎯 Длительность» → выбирает 2:30 → следующая `🧪 Тестовая генерация` Suno уйдёт с `[Length: about 2:30]` в prompt.
5. Открывает «📜 Логи» → видит последние 50 строк bot-логов прямо в Telegram, может переключить уровень на ERROR+.
6. `/captured` в DM показывает «Самое старое: YYYY-MM-DD HH:MM UTC» — retention видимо работает.

**Что НЕ делает фаза C** (это уже Phase 3+):
- Сама генерация песни на основе сообщений чата (саммаризатор + songwriter pipeline).
- Scheduler-job для «Песни дня».
- Постинг готовой песни в группу.

Для Phase 3+ инфраструктура полностью готова: `OpenRouterClient.chat(...)` ходит в выбранную модель, `system_prompt` уже в стиле songwriter, `target_duration_sec` подставляется в Suno. Останется собрать `summarize_day` + `digest_to_song` + `daily_song.py` оркестратор поверх этого.

---

## Фаза D — Song-from-chat MVP (manual trigger)

> **Все задачи ниже смерджены в [PR #29](https://github.com/pavlodrab/ideabottg/pull/29)** (`feat/song-from-chat-pipeline`) — маркеры `[x]`.

Закрывает основную владельцеву задачу:
> «Давай может добавим ии с опенроутера что бы она основываясь на контексте чата генерила песню».

MVP без scheduler — только manual trigger из меню или команды `/song_now`. Scheduler идёт отдельной фазой (3.2 / 4 в исходном плане).

- [x] **D.1** `app/services/song_pipeline.py` — оркестратор:
  - `SongDraft` (`title/style/lyrics/summary`) и `SongGenerationResult` dataclass'ы.
  - `SongPipelineError` с machine-кодами (`no_suno_key`, `no_llm_key`, `no_chat`, `too_few_messages`, `llm_call_failed`, `llm_invalid_json`, `llm_invalid_draft`, `suno_call_failed`) и `humanized()`.
  - `build_chat_text(messages)` — `@username: text` строки, фильтрует пустые / без user.
  - `trim_chat_text(text, max=100k)` — tail-bias на оверфлоу (свежие сообщения важнее).
  - `_build_user_message(chat_text, target_seconds, style_override)` — songwriter-prompt в стиле «1 куплет + припев + 1 куплет до {N} сек», поддерживает `style_override` из `chats.song_style`.
  - `llm_make_song_draft(...)` — один LLM-вызов с `response_format=json_object`, до 3 ретраев на bad JSON, `_tolerant_json_parse` для срывания markdown-обёрток (```json fences, leading "json") когда модель не слушается JSON-mode.
  - `start_song_generation(session, chat_id, requested_by)` — валидация ключей → fetch `chat_messages` за 24ч → проверка минимума → LLM → Suno в `customMode=True` (передаём наши `title`/`style`/`lyrics`) → возврат `SongGenerationResult`.
  - `watch_suno_task(...)` и `handle_terminal(...)` — портировано из `suno_admin.py`. Ключевое расширение: разделение `placeholder_chat_id` (где статус-карточка) и `audio_chat_id` (куда mp3). Этот split нужен для DM-trigger flow: статус в DM админа, mp3 в группу.
  - `Song` row пишется с `chat_id_for_song`, `style`, `lyrics` (LLM-сгенерированными) — `/musiclist` теперь будет показывать осмысленный архив для каждого чата.
- [x] **D.2** Refactor `suno_admin.py`:
  - Удалён локальный `_watch_task` и `_handle_terminal` (~150 строк), вызовы редиректятся в общий `song_pipeline.watch_suno_task`. Test-Generation flow становится частным случаем (`audio_chat_id == placeholder_chat_id`).
  - Оставлен один тонкий backwards-compat `_watch_task(...)` стаб с прежней сигнатурой — на случай если кто-то импортировал приватку.
- [x] **D.3** `app/handlers/song_admin.py` — handlers:
  - `/song_now <chat_id>` (DM, admin) — placeholder в DM, mp3 в group.
  - callback `mm:gen_pick` — chat picker под новой кнопкой «🎵 Сгенерировать песню дня» в /musicmenu.
  - callback `mm:gen:<chat_id>` — pipeline после выбора чата (placeholder = редактируется в DM, audio → group).
  - callback `music:gen_now:<chat_id>` — версия для group context (placeholder + audio в одном чате).
  - Регистрируется ПЕРЕД `music.router`, чтобы перехватить `music:gen_now:*` (формально namespace `music:`, но handler живёт в song_admin для группировки логики).
- [x] **D.4** UI:
  - `app/keyboards/musicmenu.py::musicmenu_home_keyboard` — добавлена строка с кнопкой «🎵 Сгенерировать песню дня» (callback `mm:gen_pick`).
  - `app/keyboards/music.py::music_menu_keyboard` — внизу per-chat menu добавлена кнопка «🎵 Сгенерировать песню сейчас» (callback `music:gen_now:{chat_id}`).
- [x] **D.5** Учёт стиля чата:
  - Если у чата задан `chat.song_style` (любой из 12 пресетов или кастомный текст из `/musicmenu` в группе) — он становится **override**. LLM получает инструкцию «СТИЛЬ ЗАФИКСИРОВАН — используй его в `style` JSON без изменений», и тот же текст уходит в Suno как `style`.
  - Если стиль не задан — LLM выбирает сам по тону чата (что и хотел владелец: «надо чтобы стиль автоматически выбирался»).

**Definition of done фазы D**:
1. Владелец в DM: `/musicmenu` → «🎵 Сгенерировать песню дня» → выбирает чат → видит placeholder «⏳ Готовлю…».
2. Через ~5 секунд placeholder обновляется на «🎵 task отправлена в Suno» с заголовком, стилем и summary от LLM.
3. Через 2-3 минуты — placeholder становится «✅ Готово!» с длительностью, в выбранном групповом чате появляется mp3 + lyrics.
4. Альтернативно: админ в группе — `/musicmenu` → «🎵 Сгенерировать сейчас» → placeholder и mp3 в этой же группе.
5. Альтернативно: `/song_now <chat_id>` в DM — то же что (1)+(2)+(3) минуя меню.

**Что НЕ делает фаза D**:
- Scheduler-job для автоматической ежедневной генерации (Phase 4).
- Дедуп по дате (`daily_songs.unique(chat_id, date_msk)`) — Phase 4.
- `LyricsOnlyProvider` fallback на отказ Suno — пока пайплайн просто отвечает ошибкой в placeholder.
- Per-role LLM-модели (Phase 2 в исходном дизайне — `llm_models` таблица).

---

## Фаза E — Scheduled daily song (автогенерация по расписанию)

> **Все задачи ниже — в [PR #30](https://github.com/pavlodrab/ideabottg/pull/30)** (`feat/scheduled-daily-song`). После мерджа маркеры переключаются `[~]` → `[x]`.

Достраивает над manual-триггером из Фазы D автоматическую ежедневную
генерацию: каждый opt-in чат получает cron-job, который раз в день
прогоняет `song_pipeline` и постит mp3 в сам чат. Это реализация
изначальной цели Phase 4 (scheduler + постинг), но **поверх готового
`song_pipeline`**, без отдельной таблицы `daily_songs` и map-reduce из
исходного дизайна — они остаются post-MVP (см. «Что НЕ делает фаза E»).

- [~] **E.1** Миграция `0008_chats_song_schedule` + поля модели `Chat`:
  - `chats.song_enabled` (Boolean, default false) — per-chat opt-in.
  - `chats.song_cron` (String(64), nullable) — crontab в TZ `settings.tz`.
  - `chats.last_song_sent_at` (timestamptz, nullable) — отметка последнего успешного постинга.
  - Модель `Chat` в `app/models.py` расширена тремя полями.
- [~] **E.2** `app/services/song_pipeline.py::run_scheduled_song_for_chat(bot, chat_id)` — headless-обёртка:
  - Проверяет `is_active AND song_enabled`, гоняет `start_song_generation` (requested_by=None).
  - На `too_few_messages` — **молчаливый** skip (без спама в группу). На прочих ошибках — лог, без постинга.
  - Только при успешном submit постит placeholder в группу, затем `await watch_suno_task(placeholder=audio=chat_id)`. По завершении обновляет `last_song_sent_at`.
- [~] **E.3** `app/scheduler.py` — job-тип `song:{chat_id}`:
  - `SONG_PREFIX`, `_song_job_id`, `_schedule_song`, `_run_song` (re-check enablement + quiet-hours игнорятся осознанно).
  - Загрузка всех `is_active AND song_enabled AND song_cron` чатов в `start()`.
  - `sync_chat` расширен: независимо (раз)планирует prompt-job и song-job.
- [~] **E.4** UI расписания (per-chat `/musicmenu`):
  - Статическая кнопка «📅 Расписание песни дня» в `music_menu_keyboard` (без смены сигнатуры).
  - В `song_admin.py` — подменю с пресетами времени (18:00 / 20:00 / 21:00 / 22:00) + «Выключить»; callback'и `music:song_sched:<chat_id>` / `music:song_at:<chat_id>:<hh>:<mm>` / `music:song_off:<chat_id>`. Сохраняет `song_cron`/`song_enabled` и зовёт `scheduler.sync_chat`.

**Definition of done фазы E**:
1. Админ в группе: `/musicmenu` → «📅 Расписание песни дня» → «21:00» → видит «🟢 включено · ежедневно в 21:00».
2. На следующий день в 21:00 (TZ `settings.tz`) бот сам собирает сутки чата, генерирует песню и постит mp3 + lyrics в группу.
3. В тихий день (<20 сообщений) — постинга нет, в логах строчка о пропуске.
4. «🚫 Выключить» в подменю — снимает job, автопостинг прекращается.

**Что НЕ делает фаза E** (post-MVP):
- Таблица `daily_songs` + дедуп по `(chat_id, date_msk)` — повторный ручной запуск в тот же день не блокируется.
- `LyricsOnlyProvider` fallback на отказ Suno.
- Sweep «зависших» запусков при рестарте (F8.3) и `/song_stats` — это Фаза 5.

---

## Фаза H — Публичная `/music` (песня по тексту пользователя)

> **Все задачи ниже — в открытом PR (`feat/public-music-command`), стек поверх PR #33.** После мерджа `[~]` → `[x]`.

Запрошено владельцем:
> «Надо сделать чтобы генерил песню по запросам юзеров и по их промптам,
> пример `/music Андрюха крутой чек пук стиль панк`. Но могут юзеры и без
> стиля — тогда прогонять текст через нейронку (OpenRouter), чтобы улучшил
> рифмы.»

Любой участник чата (не только админ) вызывает `/music <текст>` и
получает песню. Текст всегда прогоняется через songwriter-модель
OpenRouter (причёсывает рифмы, добавляет структуру, сохраняя смысл и
имена). Стиль — опционально в конце команды; без него модель выбирает
сама по тону.

- [~] **H.1** `song_pipeline`: вынесен общий `_llm_draft_with_retries`; добавлены `_build_prompt_user_message`, `prompt_to_song_draft` (улучшение текста → `SongDraft`) и сервис `start_song_from_prompt` (ключи → LLM → Suno submit). _(PR `feat/public-music-command`)_
- [~] **H.2** `app/handlers/song_admin.py::cmd_music` — публичная `/music` (group + DM, без admin-gate): `parse_music_command` (маркеры `стиль` / `в стиле` / `style`), per-user in-memory cooldown (180 c) + лимит длины 800, placeholder → `watch_suno_task` (mp3 в тот же чат, обложка/lyrics/fallback из общего кода). _(PR `feat/public-music-command`)_
- [~] **H.3** Тесты `tests/test_music_command.py`: парсер стиля (incl. last-marker / no-idea кейсы) + содержимое prompt-сообщения. _(PR `feat/public-music-command`)_

**Definition of done фазы H**:
1. `/music Андрюха крутой стиль панк` → через 2–3 мин mp3 в чате, стиль панк.
2. `/music песня про субботнее утро` (без стиля) → LLM сам подбирает стиль, рифмы причёсаны.
3. Спам ограничен кулдауном; при отказе (нет ключа / ошибка) кулдаун сбрасывается, юзер видит причину.

---

## Зависимости между фазами

```
Phase 0 (spec)
    │
    ├──────────────► Phase A (suno-api bot config) ──┐  (independent)
    ▼                                                │
Phase 1 (capture)  ──────────────┐                   │
    │                            │                   │
    ▼                            │                   │
Phase 2 (llm + models UI)        │                   │
    │                            │                   │
    ▼                            │                   │
Phase 3 (summarizer + songwriter)│                   │
    │                            │                   │
    ▼                            ▼                   ▼
Phase 4 (provider + orchestrator + scheduler + posting)
    │
    ▼
Phase 5 (polish) ────────► Phase 6 (optional)
```

Фазу A можно делать в любом порядке относительно 1–3: она не использует
ни `chat_messages`, ни LLM, ни оркестратор. Phase 4 потребляет результат
Phase A (готовый клиент) в виде обёртки `SunoApiOrgProvider`.

Фазу 1 и фазу 2 можно делать параллельно (разные файлы), если кому-то комфортнее. По умолчанию идём строго последовательно.

## Что нужно от владельца перед стартом фазы 4

1. **(Готово после Phase A.)** Зайти в `/suno` в боте, вставить API-ключ
   sunoapi.org и проверить, что баланс кредитов виден. После этого Phase 4
   получает доступ к Suno автоматически — env-переменные для sunoapi.org не нужны.
2. _(Опционально, как backup-провайдер.)_ Развернуть [gcui-art/suno-api](https://github.com/gcui-art/suno-api) на Railway (или VPS) и положить адрес в `SUNO_API_BASE`. Для self-hosted куки Suno остаются в env того сервиса.
3. Положить `OPENROUTER_API_KEY` в env бота на Railway.
4. В `@BotFather` → `/setprivacy` → Disable; перезайти ботом в чаты, где включаем фичу.

Без п.2 фаза 4 работает на sunoapi.org как primary; без п.1 фаза 4 завершается на «mp3 не сгенерился — fallback на lyrics-only».
