# Daily Song — tasks

Реализация спеки [`requirements.md`](./requirements.md) по дизайну [`design.md`](./design.md).

Маркеры статуса (см. `.kiro/steering/workflow.md`):
`[ ]` — не начато · `[~]` — в открытом PR · `[x]` — смерджено в основную ветку · `[!]` — заблокировано

## Прогресс по фазам

| Фаза | Что делает | Статус | Done | In PR | Total |
|------|-----------|--------|------|-------|-------|
| 0 | Спека (этот файл + requirements + design) | `[x]` | 1 | 0 | 1 |
| A | Suno API настройка через бота (sunoapi.org) — независимая от 1–6 | `[~]` | 0 | 7 | 7 |
| 1 | Capture pipeline: миграция `chat_messages`, хэндлер-логгер, конфиг-фикс | `[~]` | 4 | 1 | 5 |
| B | Music storage UI: `/musiclist` + `/musicmenu`, песни хранятся бессрочно | `[~]` | 0 | 4 | 4 |
| 2 | LLM-абстракция: OpenRouter-клиент, таблица `llm_models`, рантайм-управление через `/menu` | `[ ]` | 0 | 0 | 6 |
| 3 | Summarizer + songwriter: map-reduce, JSON-парсинг с ретраями, dry-run `/song_test` | `[ ]` | 0 | 0 | 4 |
| 4 | Song-провайдер + оркестратор: миграция `daily_songs`, SunoApiOrgProvider+SunoSelfHosted+LyricsOnly, `daily_song.py`, `/song_now`, scheduler-job, постинг в чат | `[ ]` | 0 | 0 | 7 |
| 5 | Полировка: `/song_stats`, `/song_purge`, alert при первом включении, sweep `stale_on_restart` | `[ ]` | 0 | 0 | 4 |
| 6 | Опционально: тесты-смоук, retention-cron, обложка mp3 | `[ ]` | 0 | 0 | 3 |

**Итого**: 5 / 12 / 41

## Открытые PR

| PR | Ветка | Фаза | Описание |
|----|-------|------|----------|
| [#26](https://github.com/pavlodrab/ideabottg/pull/26) | `feat/suno-api-bot-config` | A | sunoapi.org интеграция: API-ключ/модель/тестовая генерация через `/suno` в боте |

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

- [~] **A.1** Фикс пре-существующего бага `app/config.py` — `Field` использовался без импорта, бот не стартовал. Однострочный фикс: `from pydantic import Field`. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **A.2** Сервисы:
  - `app/services/settings.py` — generic k/v helpers (`get_setting` / `set_setting` / `delete_setting`) поверх существующей таблицы `settings`. Полезно и для будущей Phase 2 (`llm.active_*` ключи), и для `suno.*`.
  - `app/services/suno.py` — `SunoApiOrgClient` (httpx) c `get_credits` / `generate_music` / `get_task`, dataclass `TaskSnapshot`, helpers `mask_key` / `get_api_key` / `set_api_key` / `clear_api_key` / `get_model` / `set_model` / `get_callback_url`. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **A.3** UI:
  - `app/keyboards/suno.py` — главное меню Suno, выбор модели, бэк-кнопка, подтверждение удаления ключа.
  - `app/handlers/suno_admin.py` — команды `/suno`, `/suno_credits`, `/suno_status <task_id>`; callback'и `suno:home` / `suno:set_key` / `suno:remove_key{,_yes}` / `suno:credits` / `suno:model_open` / `suno:model_set:<slug>` / `suno:gen_open`; FSM-стейты `SunoApiKeyEditing`, `SunoTestPrompt` в `app/states.py`.
  - Кнопка «🎵 Suno API» в `home_keyboard`.
  - Регистрация роутера в `app/handlers/__init__.py` после `admin_users` и до `chats` (DM-only хэндлеры). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **A.4** Тестовая генерация:
  - При вводе ключа сначала валидируем `GET /api/v1/generate/credit` — если не 200, не сохраняем.
  - При тестовой генерации вызываем `POST /api/v1/generate` в режиме `customMode=false, instrumental=false` (только prompt, lyrics auto), и стартуем фоновый поллер через `asyncio.create_task` с интервалом 15 сек, таймаутом 360 сек. Когда задача в `SUCCESS` — отправляем mp3 как `send_audio(audio_url)`, на фейл/таймаут — редактируем плейсхолдер.
  - Сообщение с ключом удаляется из истории чата сразу после получения, чтобы ключ не светился. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **A.5** Спека / зависимости / `.env.example`:
  - `httpx==0.27.2` в `requirements.txt`.
  - `.env.example` — секция отмечает, что для Suno env не нужен (всё через `/suno`).
  - Этот файл (`tasks.md`) и `design.md` обновлены: §3.6 описывает `SunoApiOrgProvider` как готовый к интеграции в Phase 4, §7 разделён на 7.1 (Suno в БД) и 7.2 (daily-song env). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **A.6** Хардинг под реальные доки sunoapi.org:
  - `TaskSnapshot.from_response` теперь принимает обе формы списка треков из доков — OpenAPI `response.sunoData[]` (camelCase) И Quickstart `response.data[]` (snake_case).
  - `get_credits` сначала пробует `/api/v1/generate/credit` (OpenAPI), на 404 падает на `/api/v1/get-credits` (Quickstart-сэмпл) — официальная дока противоречит сама себе.
  - `STATUS_FAILED` добавлен в `TERMINAL_STATUSES` (упомянут в Quickstart-прозе, отсутствует в OpenAPI-enum). Неизвестные статусы (`GENERATING`, etc.) трактуем как нетерминальные — лучше лишний раз популить, чем зависнуть.
  - `TaskSnapshot.error_message` парсит `data.errorMessage` (есть в OpenAPI-схеме) — теперь юзеру в фейле видна причина из API, а не только статус-код.
  - `SunoApiError.humanized()` — гуманайзер кодов ошибок Suno (401/413/429/430/455/etc.) на русский. Используется во всех местах вывода ошибок (валидация ключа, баланс, генерация, статус). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
  - В success-сообщении тестовой генерации появилась подсказка про 15-дневный retention файлов на серверах Suno.
- [~] **A.7** Архив сгенерированных песен + захват Telegram `file_id`:
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

Цель: бот умеет добавлять/активировать/удалять модели OpenRouter через `/menu`, и есть рабочий `OpenRouterClient.chat(...)`. Без summarizer-логики самой.

- [ ] **2.1** Alembic-миграция `20260613_0007_llm_models.py`: таблица `llm_models` (см. design §2.4). `settings`-таблица уже есть, ничего не трогаем.
- [ ] **2.2** Модель `LlmModel` в `app/models.py`.
- [ ] **2.3** Сервис `app/services/llm_models.py`: CRUD + `get_active(role)` + `set_active(role, slug)`. Хранение активной модели — в `settings` (`llm.active_summarizer`/`llm.active_songwriter`).
- [ ] **2.4** Сервис `app/services/llm.py`: `OpenRouterClient` поверх `httpx`. Добавить `httpx==0.27.2` в `requirements.txt`.
- [ ] **2.5** FSM-стейты `LlmModelAdd`, `LlmModelEditPrompt` в `app/states.py`.
- [ ] **2.6** UI в `app/handlers/admin_menu.py` (или отдельный `song_admin.py`): новая кнопка «🤖 LLM-модели» в главном меню, экран списка с маркерами активных ролей, добавление/редактирование/удаление по визарду. Callback-префикс `song:model:*`. Запрет удаления активной модели.

**Definition of done фазы 2**: владелец через DM-меню добавляет модель `meta-llama/llama-3.3-70b-instruct:free`, активирует её на обе роли, и `await get_active("summarizer")` возвращает её. Реальный `OpenRouterClient.chat()` тестируется ручным smoke-тестом владельцем.

---

## Фаза 3 — Summarizer + songwriter (без Suno)

Цель: на сохранённой истории бот умеет получить `SongDraft` и показать его в ЛС админу через `/song_test`. Suno ещё не подключаем.

- [ ] **3.1** Сервис `app/services/song_summarizer.py`: `summarize_day` с map-reduce, JSON-парсинг с 2 ретраями.
- [ ] **3.2** Сервис `app/services/song_writer.py`: `digest_to_song`, дефолтный system-prompt (см. design §3.5).
- [ ] **3.3** Команда `/song_test <chat_id>` в `song_admin.py`: dry-run, показывает в ЛС: число сообщений, дайджест JSON, SongDraft JSON. Не пишет в `daily_songs`.
- [ ] **3.4** Smoke-проверка: `/song_test` на чате с накопленной за фазу 1+2 историей. Если сообщений `< SONG_MIN_MESSAGES` — корректное сообщение «недостаточно».

**Definition of done фазы 3**: `/song_test` для реального чата возвращает валидный SongDraft.

---

## Фаза 4 — Song-провайдер + оркестратор + scheduler + постинг

Цель: полный пайплайн работает; cron каждый день в 21:00 MSK постит mp3 в чат.

- [ ] **4.1** Alembic-миграция `20260613_0008_daily_songs.py`: таблица `daily_songs` (см. design §2.3).
- [ ] **4.2** Модель `DailySong` в `app/models.py`.
- [ ] **4.3** Сервис `app/services/song_provider.py`: `SongProvider` Protocol + `SunoApiOrgProvider` (обёртка поверх `app/services/suno.py::SunoApiOrgClient` из Phase A) + `SunoSelfHostedProvider` + `LyricsOnlyProvider` + фабрика. Выбор активного провайдера — через ключ `suno.provider` в БД (default `sunoapi_org`); env остаются только для `SUNO_API_BASE` (адрес self-hosted backup).
- [ ] **4.4** Сервис `app/services/daily_song.py`: оркестратор `run_daily_song_for_chat`, `post_song_to_chat`. Транзакционные апдейты статуса. Fallback на `LyricsOnlyProvider` при таймауте Suno.
- [ ] **4.5** Расширить `app/scheduler.py`: новый job-тип `song:{chat_id}`, `_schedule_song`, `_run_song`, регистрация в `start()` и `sync_chat()`.
- [ ] **4.6** Команда `/song_now <chat_id>` в `song_admin.py`: ручной trigger пайплайна с записью в БД.
- [ ] **4.7** Toggle «🎵 Песня дня» в `chat_settings_keyboard`. Callback `song:toggle:{chat_id}`. При первом включении — alert N1.2.

**Definition of done фазы 4**: `/song_now` для тест-чата → mp3 в чате; следующий день в 21:00 MSK — сработает по cron автоматически.

---

## Фаза 5 — Полировка

- [ ] **5.1** `/song_stats` — счётчики статусов за 30 дней, по чатам.
- [ ] **5.2** `/song_purge <chat_id>` — удаление истории чата с inline-confirm. Только OWNER.
- [ ] **5.3** Sweep при старте (F8.3): `daily_songs` со статусом `queued`/`generating` старше 24ч → `failed, error="stale_on_restart"`.
- [ ] **5.4** Маскировать `OPENROUTER_API_KEY` и `SUNO_API_BASE` в логах (расширить `database_url_masked`-подход).

---

## Фаза 6 — Опционально (post-MVP)

- [ ] **6.1** Smoke-тесты для `summarize_day` / `digest_to_song` / `LyricsOnlyProvider` (только если попросят).
- [ ] **6.2** Retention-cron: `chat_messages` старше `SONG_RETENTION_DAYS` (default 30) удалять.
- [ ] **6.3** Обложка: пробросить `image_url` из Suno-ответа, прикладывать к посту.

---

## Фаза 1 — Capture pipeline

- [x] **1.1** Миграция `0006_chat_messages_and_songs`: новая таблица `chat_messages` (id, chat_id FK, tg_message_id, user_id, username, full_name, text, created_at) + индексы и UniqueConstraint(chat_id, tg_message_id) inline (sqlite-friendly). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **1.2** Модель `ChatMessage` в `app/models.py` + сервис `app/services/chat_messages.py` (`insert_message` с дедупом по unique-constraint, `count_messages`, `fetch_messages_since`, `delete_older_than`, `cutoff_for_retention`). RETENTION_DAYS=2, MAX_TEXT_LEN=2000. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **1.3** Capture middleware `app/middlewares/capture.py`: ловит текст и captions из group/supergroup, пропускает ботов, команды (`/`), не-text сообщения, не-зарегистрированные и paused чаты. Всегда swallow exceptions — никогда не блокирует основной handler-chain. Регистрируется ПОСЛЕ `DbSessionMiddleware` чтобы иметь `data["session"]`. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [x] **1.4** Retention scheduler-job в `IdeaScheduler.start()`: cron `5 * * * *` (каждый час в xx:05), запускает `delete_older_than(cutoff_for_retention(2))`. Логирует число удалённых строк. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **1.5** Команда `/captured [chat_id]` в `app/handlers/music.py` — DM-only, admin-only диагностика: показывает 24h-window и total-в-окне-retention для каждого зарегистрированного чата (без аргумента) или для одного (с chat_id-аргументом). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_

---

## Фаза B (новая) — Music storage UI поверх Suno

Параллельная фаза, реализованная вместе с Phase 1: даёт юзерам видеть и
проигрывать сгенерированные песни прямо в Telegram, и админам настраивать
дефолтный стиль для каждого чата.

- [~] **B.1** `/musiclist` в `app/handlers/music.py` — открыто всем, без admin-gate. В групповом чате показывает песни этого чата; в DM показывает песни юзера (по `requested_by`), а админ видит весь cross-chat архив. По 5 на страницу, кнопки `▶️ N` отправляют mp3 в-place через `send_audio`. После первого `send_audio` Telegram `file_id` сохраняется в `Song.tg_audio_file_id` и используется для всех последующих воспроизведений (бессмертно). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **B.2** `/musicmenu` — admin-only, выбор дефолтного стиля для чата. В группе сразу открывает меню для текущего чата; в DM показывает chat-picker. 12 пресетов (Pop / Rock / Lo-fi / Folk / Synthwave / Hip-hop / Classical / Jazz / Electronic / Ambient / Indie / Metal) + Custom (FSM-ввод произвольного текста до 500 символов) + Reset. Сохраняется в `chats.song_style` (новая колонка). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **B.3** Keyboards `app/keyboards/music.py`: `music_list_keyboard` (play row + pagination), `music_menu_keyboard` (12 presets с ✅-маркером + Custom + Reset), `music_chat_picker_keyboard` (DM выбор чата), `music_style_back_keyboard`. Callback-namespace `music:*` (без коллизий с `suno:*` / `chat:*` / `prompt:*` / etc.). _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_
- [~] **B.4** FSM `MusicCustomStyle.waiting_text` в `app/states.py` для свободного ввода кастомного стиля. _(PR [#26](https://github.com/pavlodrab/ideabottg/pull/26))_

**Definition of done фазы B**: юзер в групповом чате пишет `/musiclist` → видит ленту песен, тапает `▶️ 1` → бот присылает mp3 в чат. Админ в той же группе пишет `/musicmenu` → выбирает «🌙 Lo-fi» → следующая Phase-4 daily-song будет генериться в этом стиле.

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
