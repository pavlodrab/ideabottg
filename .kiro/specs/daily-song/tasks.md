# Daily Song — tasks

Реализация спеки [`requirements.md`](./requirements.md) по дизайну [`design.md`](./design.md).

Маркеры статуса (см. `.kiro/steering/workflow.md`):
`[ ]` — не начато · `[~]` — в открытом PR · `[x]` — смерджено в основную ветку · `[!]` — заблокировано

## Прогресс по фазам

| Фаза | Что делает | Статус | Done | In PR | Total |
|------|-----------|--------|------|-------|-------|
| 0 | Спека (этот файл + requirements + design) | `[~]` | 0 | 1 | 1 |
| 1 | Capture pipeline: миграция `chat_messages`, хэндлер-логгер, конфиг-фикс | `[ ]` | 0 | 0 | 5 |
| 2 | LLM-абстракция: OpenRouter-клиент, таблица `llm_models`, рантайм-управление через `/menu` | `[ ]` | 0 | 0 | 6 |
| 3 | Summarizer + songwriter: map-reduce, JSON-парсинг с ретраями, dry-run `/song_test` | `[ ]` | 0 | 0 | 4 |
| 4 | Song-провайдер + оркестратор: миграция `daily_songs`, SunoSelfHosted+LyricsOnly, `daily_song.py`, `/song_now`, scheduler-job, postинг в чат | `[ ]` | 0 | 0 | 7 |
| 5 | Полировка: `/song_stats`, `/song_purge`, alert при первом включении, sweep `stale_on_restart` при старте | `[ ]` | 0 | 0 | 4 |
| 6 | Опционально: тесты-смоук, retention-cron, обложка mp3 | `[ ]` | 0 | 0 | 3 |

**Итого**: 0 / 0 / 30

## Открытые PR

| PR | Ветка | Фаза | Описание |
|----|-------|------|----------|
| _(пусто)_ | `spec/daily-song` | 0 | Сама спека (этот PR — будет проставлен после `gh pr create`) |

---

## Фаза 0 — Спека

- [~] **0.1** Написать requirements / design / tasks + steering `workflow.md` _(этот PR)_

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
- [ ] **4.3** Сервис `app/services/song_provider.py`: `SongProvider` Protocol + `SunoSelfHostedProvider` + `LyricsOnlyProvider` + фабрика. `SUNO_API_BASE`, `SONG_PROVIDER` в config + `.env.example`.
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

## Зависимости между фазами

```
Phase 0 (spec)
    │
    ▼
Phase 1 (capture)  ──────────────┐
    │                            │
    ▼                            │
Phase 2 (llm + models UI)        │
    │                            │
    ▼                            │
Phase 3 (summarizer + songwriter)│
    │                            │
    ▼                            │
Phase 4 (provider + orchestrator + scheduler + posting)
    │
    ▼
Phase 5 (polish) ────────► Phase 6 (optional)
```

Фазу 1 и фазу 2 можно делать параллельно (разные файлы), если кому-то комфортнее. По умолчанию идём строго последовательно.

## Что нужно от владельца перед стартом фазы 4

1. Развернуть [gcui-art/suno-api](https://github.com/gcui-art/suno-api) на Railway (или VPS) и сообщить `SUNO_API_BASE`. Куки Suno — в env того сервиса, не нашего бота.
2. Положить `OPENROUTER_API_KEY` в env бота на Railway.
3. В `@BotFather` → `/setprivacy` → Disable; перезайти ботом в чаты, где включаем фичу.

Без п.1 фаза 4 завершается на «mp3 не сгенерился — fallback на lyrics-only». Это OK как промежуточный результат.
