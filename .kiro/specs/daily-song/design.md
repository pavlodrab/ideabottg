# Daily Song — design

Реализация требований из [`requirements.md`](./requirements.md). Фича — отдельный модуль поверх существующего `app/`, ложится в текущие конвенции (handlers/services/keyboards, FSM, APScheduler, Alembic).

## 1. Высокоуровневая схема

```
                     ┌────────────────────────────────────────┐
                     │             ideabottg (aiogram)        │
                     └────────────────────────────────────────┘
                                       │
       ┌───────────────────────────────┼───────────────────────────────────┐
       │                               │                                   │
┌──────▼─────────┐         ┌───────────▼──────────┐            ┌───────────▼──────────┐
│ chat_logger    │         │  IdeaScheduler       │            │  admin_menu UI       │
│ (handler)      │         │  + song job per chat │            │  toggle / models /   │
│ записывает все │         │  cron 0 21 * * *     │            │  /song_test          │
│ сообщения чата │         │  TZ=Europe/Moscow    │            │                      │
│ в chat_messages│         └───────────┬──────────┘            └──────────────────────┘
└──────┬─────────┘                     │
       │ INSERT                        │ on tick
       ▼                               ▼
┌──────────────┐         ┌──────────────────────────┐
│ chat_messages│◄────────│  daily_song.run(chat,T)  │
│ (table)      │ SELECT  │  ── оркестратор ──       │
└──────────────┘ window  │                          │
                         │  1. fetch window         │
                         │  2. summarizer (map)     │      ┌──────────────────┐
                         │  3. summarizer (reduce)  │─────►│ OpenRouterClient │
                         │  4. songwriter           │      │ (chat completions│
                         │  5. SongProvider.submit  │      │  via httpx)      │
                         │  6. SongProvider.poll    │      └──────────────────┘
                         │  7. post to chat         │
                         │  8. update daily_songs   │      ┌──────────────────┐
                         └──────────┬───────────────┘─────►│ SongProvider     │
                                    │                      │ (self_hosted /   │
                                    │                      │  lyrics_only)    │
                                    │                      └──────────────────┘
                                    ▼
                            ┌──────────────┐
                            │ daily_songs  │
                            │ (table)      │
                            └──────────────┘
```

## 2. Изменения в БД

Все три объекта — одной Alembic-миграцией `20260613_0006_daily_song.py` (имя файла унифицировано с существующими `20260607_*`).

### 2.1. Изменение `chats`

```sql
ALTER TABLE chats
  ADD COLUMN song_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN song_started_at TIMESTAMPTZ NULL,  -- когда впервые включили (для UI)
  ADD COLUMN last_song_sent_at TIMESTAMPTZ NULL;
```

### 2.2. Новая таблица `chat_messages`

```sql
CREATE TABLE chat_messages (
    id              BIGSERIAL PRIMARY KEY,
    chat_id         BIGINT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    tg_message_id   BIGINT NOT NULL,
    from_user_id    BIGINT NOT NULL,
    from_username   VARCHAR(64) NULL,
    text            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_chat_messages_chat_created ON chat_messages (chat_id, created_at DESC);
CREATE UNIQUE INDEX ux_chat_messages_chat_msg ON chat_messages (chat_id, tg_message_id);
```

`UNIQUE(chat_id, tg_message_id)` — защита от дубль-апдейтов (edit_message может прилететь второй раз; см. §3.1).

### 2.3. Новая таблица `daily_songs`

```sql
CREATE TABLE daily_songs (
    id                BIGSERIAL PRIMARY KEY,
    chat_id           BIGINT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    date_msk          DATE NOT NULL,         -- дата в SONG_TZ
    status            VARCHAR(16) NOT NULL,  -- queued|generating|done|skipped|failed
    provider          VARCHAR(32) NULL,      -- self_hosted|lyrics_only|...
    provider_task_id  VARCHAR(128) NULL,
    audio_url         TEXT NULL,
    title             TEXT NULL,
    lyrics            TEXT NULL,
    style             TEXT NULL,
    n_messages        INTEGER NULL,
    error             TEXT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at       TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX ux_daily_songs_chat_date ON daily_songs (chat_id, date_msk);
```

### 2.4. Новая таблица `llm_models`

```sql
CREATE TABLE llm_models (
    id              SERIAL PRIMARY KEY,
    slug            VARCHAR(128) NOT NULL UNIQUE,   -- 'meta-llama/llama-3.3-70b-instruct:free'
    display_name    VARCHAR(128) NOT NULL,
    role            VARCHAR(16) NOT NULL,            -- summarizer|songwriter|both
    system_prompt   TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Активная модель на роль хранится в существующей таблице `settings`:
- ключ `llm.active_summarizer` → значение = `slug`
- ключ `llm.active_songwriter` → значение = `slug`

## 3. Сервисы (`app/services/`)

### 3.1. `chat_messages.py`

```python
async def record_message(session, message: aiogram.types.Message) -> None:
    """Идемпотентно пишет message.text в chat_messages.

    Игнор: команды (text starts with '/'), служебные (new_chat_members и т.п.),
    DM/каналы, чат с song_enabled=False, пустой text.
    Edit-апдейты: при IntegrityError на (chat_id, tg_message_id) — UPDATE text.
    """

async def fetch_window(session, chat_id: int, start_utc, end_utc, limit: int)
    -> list[ChatMessage]:
    """Сообщения окна, ASC по created_at, обрезаны до limit (берём свежие)."""

async def purge_chat_history(session, chat_id: int) -> int:
    """DELETE FROM chat_messages WHERE chat_id=...; вернёт count."""
```

### 3.2. `llm.py`

`OpenRouterClient` — тонкая обёртка над `httpx.AsyncClient`, OpenAI-совместимый chat-completions:

```python
class OpenRouterClient:
    BASE = "https://openrouter.ai/api/v1"

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        response_format: dict | None = None,   # {"type": "json_object"}
        timeout: float = 60.0,
    ) -> str: ...
```

Headers:
```
Authorization: Bearer {OPENROUTER_API_KEY}
HTTP-Referer:  https://github.com/pavlodrab/ideabottg
X-Title:       IdeaBot Daily Song
```

Ретраи: 3 попытки с экспонентой 1→4→16 сек на 429/5xx/timeout. Логируем `model`, `prompt_tokens`, `completion_tokens` (из ответа) для observability.

> **MVP simplification (Phase C, реализовано):** в первом релизе бот
> работает с **одной** активной моделью OpenRouter и **одним** общим
> system prompt — обе настройки хранятся прямо в существующей таблице
> `settings` под ключами `llm.api_key`, `llm.model`,
> `llm.system_prompt`, `llm.referer`. Управление — через
> `/musicmenu → 🤖 OpenRouter` (см. `app/handlers/llm_admin.py`).
>
> Это означает, что `summarizer` и `songwriter` шаги Phase 3 будут
> исполняться одной и той же моделью с одним и тем же prompt. Для
> MVP это ок — Gemini 2.0 Flash (`google/gemini-2.0-flash-exp:free`,
> бесплатный default) тянет оба шага в одном LLM-вызове.
>
> Полная схема с таблицей `llm_models` и per-role активацией ниже
> (§3.3) остаётся как **post-MVP расширение** — мигрируем на неё, если
> упрёмся в качество одной модели или захотим раздельные prompts.
> Переход назад-совместимый: `llm.model` → `llm.active_summarizer` /
> `llm.active_songwriter`, плюс новая таблица `llm_models`.

### 3.3. `llm_models.py` (CRUD + активация) — post-MVP

```python
async def list_models(session, role: str | None = None) -> list[LlmModel]: ...
async def add_model(session, slug, display_name, role, system_prompt) -> LlmModel: ...
async def remove_model(session, model_id) -> bool: ...
async def get_active(session, role: Literal["summarizer", "songwriter"])
    -> LlmModel | None: ...
async def set_active(session, role, slug) -> None: ...
```

`get_active` → читает из `settings`, JOIN'ом с `llm_models` находит запись (роль модели в БД должна быть `summarizer`/`both` или `songwriter`/`both` соответственно).

### 3.4. `song_summarizer.py`

```python
@dataclass
class DayDigest:
    date_msk: date
    n_messages: int
    topics: list[str]      # 3-7
    vibe: str              # 1-2 предложения, общее настроение
    key_quotes: list[str]  # 3-5 цитат, отредактированных LLM
    top_users: list[str]   # @ники

async def summarize_day(
    llm: OpenRouterClient,
    summarizer_slug: str,
    system_prompt: str | None,
    messages: list[ChatMessage],
    chunk_size: int = 50,
) -> DayDigest: ...
```

Алгоритм:
1. Группируем `messages` в чанки по `chunk_size`.
2. Для каждого чанка — вызов LLM с system="ты делаешь краткую выжимку чата" + user=форматированный список `«@user: текст»`. Ответ: ≤200 токенов с темами/мемами/цитатами.
3. Reduce-вызов: system="ты объединяешь N выжимок в один структурированный JSON" + user=все чанк-выжимки. Ответ — JSON с полями DayDigest. Используем `response_format={"type":"json_object"}`. На невалидный JSON — 2 ретрая.

### 3.5. `song_writer.py`

```python
@dataclass
class SongDraft:
    title: str
    lyrics: str    # ≤600 chars
    style: str     # ≤200 chars, в формате Suno style prompt
    tags: list[str]

async def digest_to_song(
    llm: OpenRouterClient,
    songwriter_slug: str,
    system_prompt: str | None,
    digest: DayDigest,
) -> SongDraft: ...
```

System-промпт по умолчанию (если в `llm_models.system_prompt` пусто):

```
Ты — songwriter. На входе — дайджест дня чата (темы, vibe, цитаты, активные участники).
Верни СТРОГО JSON без префиксов и комментариев:
{
  "title": "<до 60 символов>",
  "lyrics": "<куплет-припев-куплет, до 600 символов, на языке дайджеста>",
  "style": "<жанр + 3-5 ключевых слов настроения, до 200 символов, английский>",
  "tags": ["<жанр>", "<настроение>", ...]
}
Не цитируй сообщения дословно — пересказывай. Используй ники только если они органичны.
```

### 3.6. `song_provider.py`

```python
class SongProvider(Protocol):
    name: str
    async def submit(self, draft: SongDraft) -> str: ...    # task_id
    async def poll(self, task_id: str) -> SongResult | None: ...

@dataclass
class SongResult:
    audio_url: str | None   # None для lyrics_only
    title: str
    lyrics: str
    style: str
```

**`SunoApiOrgProvider`** (default, реализован в Phase A) — клиент к публичному
шлюзу [sunoapi.org](https://sunoapi.org) (платный, без self-hosted). База —
`https://api.sunoapi.org`, авторизация — Bearer-token из настроек бота
(см. §7). Все параметры — API-ключ, модель, callback URL — хранятся в БД
в таблице `settings` и редактируются админом через `/menu → 🎵 Suno API`
(см. `app/handlers/suno_admin.py`). Никаких env-переменных для Suno.

- `submit` → `POST /api/v1/generate` с
  `{"prompt": draft.lyrics, "customMode": true, "instrumental": false,
    "model": <выбранная модель>, "style": draft.style, "title": draft.title,
    "callBackUrl": <dummy URL>}` → ответ `{"data": {"taskId": "..."}}`.
- `poll` → `GET /api/v1/generate/record-info?taskId=...` — возвращает
  `status` ∈ `{PENDING, TEXT_SUCCESS, FIRST_SUCCESS, SUCCESS, *_FAILED, ...}`
  и `response.sunoData[]` со списком треков (мы берём первый, у него
  `audioUrl` появляется при `SUCCESS`).

Эта реализация **уже доступна в Phase A** в виде `app/services/suno.py`
(класс `SunoApiOrgClient`). Орxестратор `daily_song.py` (Phase 4) поверх
неё построит `SunoApiOrgProvider`-обёртку, реализующую протокол
`SongProvider`.

**`SunoSelfHostedProvider`** (реализуется в Phase 4 как backup) — клиент
к self-hosted [gcui-art/suno-api](https://github.com/gcui-art/suno-api),
живёт в env (т.к. это адрес внутреннего сервиса, не секрет аккаунта).
POST `{SUNO_API_BASE}/api/generate` с body
`{"prompt": draft.lyrics, "tags": draft.style, "title": draft.title,
"make_instrumental": false, "wait_audio": false}` → массив clip-ов с
id; берём первый id как `task_id`. `poll` — GET `/api/get?ids={id}`
пока `status == "complete"` и `audio_url` не пуст.

**`LyricsOnlyProvider`** — `submit` синхронно возвращает sentinel
`"lyrics-only"`, `poll` сразу даёт `SongResult(audio_url=None, ...)` из
исходного draft. Используется как фоллбэк, если основной провайдер
отвалился или таймаутнулся.

Фабрика:
```python
def get_song_provider(session) -> SongProvider:
    # `song_provider` теперь тоже в settings (key=`suno.provider`),
    # значения: 'sunoapi_org' (default), 'self_hosted', 'lyrics_only'
    name = await get_setting(session, "suno.provider") or "sunoapi_org"
    if name == "sunoapi_org":
        api_key = await get_api_key(session)
        return SunoApiOrgProvider(api_key=api_key, ...)
    if name == "self_hosted":
        return SunoSelfHostedProvider(base=settings.suno_api_base, ...)
    if name == "lyrics_only":
        return LyricsOnlyProvider()
    raise ValueError(...)
```

### 3.7. `daily_song.py` — оркестратор

```python
async def run_daily_song_for_chat(
    bot: Bot,
    session_factory: sessionmaker,
    chat_id: int,
    now_msk: datetime,
) -> None:
    """Полный пайплайн. Запускается из APScheduler-job или из /song_now."""
```

Шаги (каждый — отдельная DB-транзакция, статус `daily_songs` обновляется по факту):

1. `INSERT ... ON CONFLICT(chat_id,date_msk) DO NOTHING RETURNING id` — если `None`, проверяем existing: статус `done`/`generating` → выходим, `failed` → ретраим (UPDATE status=queued).
2. Окно: `start_utc = day_msk_00 → UTC`, `end_utc = now_msk → UTC`. `messages = fetch_window(...)`.
3. Если `len(messages) < SONG_MIN_MESSAGES` → `status=skipped, error="below_threshold:N"`, return.
4. `summarizer = await get_active("summarizer")`; если `None` → `failed, no_active_model`, notify owner, return.
5. `digest = await summarize_day(...)` → `status=generating`.
6. `songwriter = await get_active("songwriter")`; та же проверка.
7. `draft = await digest_to_song(...)`.
8. `provider = get_song_provider()`. `task_id = await provider.submit(draft)`. Запоминаем в `daily_songs.provider_task_id`.
9. Поллинг с интервалом 15s, таймаут `SONG_GENERATION_TIMEOUT`.
10. **Fallback**: если поллинг таймаутнулся или провайдер вернул ошибку — переключаемся на `LyricsOnlyProvider` для этого запуска.
11. Постим в чат (см. §4). На успехе — `status=done, finished_at=now()`, `chats.last_song_sent_at=now()`.
12. На любом исключении выше точки «постинг» — `status=failed, error=str(exc)`, лог error, notify owner.

### 3.8. `quiet_hours.py` — без изменений

Песенный job **не** проходит через `should_send_proactive` (это запрошенное юзером 21:00, явно вне quiet окна).

## 4. Постинг в чат

Helper в `services/daily_song.py`:

```python
async def post_song_to_chat(
    bot: Bot, chat: Chat, draft: SongDraft, audio_url: str | None, n_messages: int
) -> None:
    caption = (
        f"🎵 <b>Песня дня</b> · {html.escape(draft.title)}\n\n"
        f"📊 {n_messages} сообщений · 00:00–{datetime.now().strftime('%H:%M')} MSK\n"
        f"🎨 <i>{html.escape(draft.style)}</i>"
    )
    if audio_url:
        await bot.send_audio(chat.chat_id, audio=audio_url, caption=caption,
                             title=draft.title, performer="IdeaBot")
    else:
        await bot.send_message(
            chat.chat_id,
            f"🎵 <b>Песня дня (только текст — Suno недоступен)</b>\n\n"
            f"<b>{html.escape(draft.title)}</b>\n\n"
            f"<i>{html.escape(draft.style)}</i>"
        )
    # lyrics — отдельным сообщением с code-blocks
    safe = html.escape(draft.lyrics)[:4000]
    await bot.send_message(chat.chat_id, f"<pre>{safe}</pre>")
```

## 5. Scheduler (`app/scheduler.py`)

Добавляем третий тип job: `song:{chat_id}`. По аналогии с `_schedule_prompt`/`sync_chat`, но:

- Cron берётся из `settings.song_cron` (не из БД per-chat).
- Timezone — `settings.song_tz` (не глобальный `settings.tz`).
- Условие активности: `chat.is_active AND chat.song_enabled`.
- Метод-обёртка `sync_chat` (уже существует) расширяется: после prompt-job-логики ещё переcбрасывает song-job.

Псевдокод дополнения:

```python
SONG_PREFIX = "song:"

def _song_job_id(chat_id: int) -> str: return f"{SONG_PREFIX}{chat_id}"

async def sync_chat(self, chat_id: int) -> None:
    ...existing prompt logic...
    # song side
    if chat is None or not chat.is_active or not chat.song_enabled or not settings.song_enabled:
        self._remove_job(_song_job_id(chat_id))
        return
    self._schedule_song(chat.chat_id)

def _schedule_song(self, chat_id: int) -> None:
    trigger = CronTrigger.from_crontab(settings.song_cron, timezone=settings.song_tz)
    self._scheduler.add_job(self._run_song, trigger=trigger,
                            id=_song_job_id(chat_id), args=[chat_id],
                            replace_existing=True, misfire_grace_time=600,
                            coalesce=True, max_instances=1)

async def _run_song(self, chat_id: int) -> None:
    from app.services.daily_song import run_daily_song_for_chat
    now = datetime.now(ZoneInfo(settings.song_tz))
    await run_daily_song_for_chat(self.bot, SessionLocal, chat_id, now)
```

В `start()`: добавляем загрузку всех `chat.song_enabled and chat.is_active` и `_schedule_song` для каждого. Также при старте — sweep «зависших» `daily_songs` (см. требование F8.3).

## 6. Handlers

### 6.1. `app/handlers/chat_logger.py` (новый)

```python
router = Router(name="chat_logger")

@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text)
async def log_chat_message(message: Message, session: AsyncSession) -> None:
    if message.text and message.text.startswith("/"):
        return
    if message.from_user is None or message.from_user.is_bot:
        return
    chat = await session.get(Chat, message.chat.id)
    if chat is None or not chat.song_enabled:
        return
    await record_message(session, message)
```

**Важно**: этот роутер регистрируется **после** `ideas` (там reply-capture на призыв), чтобы reply-флоу не сломался. Aiogram routes — first match wins per filter, но обе функции используют разные фильтры (reply_to_message vs general text), порядок имеет значение для overlap. Проверяем в фазе 1.

### 6.2. `app/handlers/song_admin.py` (новый)

Команды (DM, only admins):
- `/song_test <chat_id>` — dry-run, шлёт промежутки в DM.
- `/song_now <chat_id>` — реальный запуск (write to DB, post to chat).
- `/song_stats` — табличка статистики.
- `/song_purge <chat_id>` — удалить историю чата (только OWNER, с inline-confirm).

Callback'и для нового UI меню (см. 6.3).

### 6.3. Расширение `app/handlers/admin_menu.py`

В `chat_settings_keyboard` добавляем кнопку:

```
🎵 Песня дня: 🟢/🔴   (callback: song:toggle:{chat_id})
🤖 LLM-модели           (callback: song:models)
```

Новые callback handlers:
- `song:toggle:{chat_id}` — переключает `song_enabled`, синкает scheduler, при первом включении показывает alert с N1.2-предупреждением.
- `song:models` → список моделей с активными ролями.
- `song:model:add` → FSM-визард `LlmModelAdd` (states: `waiting_slug → waiting_name → waiting_role → waiting_system_prompt`).
- `song:model:open:{id}` → карточка с кнопками: «activate summarizer», «activate songwriter», «edit prompt», «delete».
- `song:model:activate:{id}:{role}` → `set_active`.
- `song:model:delete:{id}` → проверка «не активна» → confirm → delete.

Callback префикс `song:` уникальный, не пересекается с `chat:`/`sched:`/`prompt:`/`card:`/`admin:`/`tag:`/`anon:`.

### 6.4. `app/states.py`

```python
class LlmModelAdd(StatesGroup):
    waiting_slug = State()
    waiting_display_name = State()
    waiting_role = State()
    waiting_system_prompt = State()

class LlmModelEditPrompt(StatesGroup):
    waiting_text = State()

class SongPurgeConfirm(StatesGroup):
    waiting_confirm = State()
```

## 7. Конфиг (`app/config.py` + `.env.example` + DB `settings`)

**Принцип**: всё, что админ может захотеть менять без редеплоя, живёт в БД
(таблица `settings`, см. §2 — она уже есть). Env остаётся только для
секретов уровня инфраструктуры (BOT_TOKEN, DATABASE_URL) и параметров,
требующих рестарта (TZ, LOG_LEVEL).

### 7.1. Suno (Phase A — DB-backed, реализовано)

Хранится в таблице `settings` под ключами:

| key                 | где используется                       | дефолт                                     |
|---------------------|----------------------------------------|--------------------------------------------|
| `suno.api_key`      | Bearer для `api.sunoapi.org`           | (не задан → UI просит ввести)              |
| `suno.model`        | модель по умолчанию                    | `V4_5`                                     |
| `suno.callback_url` | поле `callBackUrl` в `/api/v1/generate`| `https://example.com/suno-callback` (мок)  |
| `suno.provider`     | (Phase 4) выбор провайдера             | `sunoapi_org`                              |

UI — `/suno` или «🎵 Suno API» в `/menu`. См. `app/handlers/suno_admin.py`
и `app/services/suno.py`.

**Никаких env-переменных для sunoapi.org не нужно.** Owner деплоит бота
без секретов Suno и потом задаёт ключ в Telegram.

### 7.2. Daily-song оркестрация (Phase 1+)

Эти параметры либо уровень фичи, либо настройка LLM-шлюза, который
тоже ключи требует. Идём по тому же принципу — секреты в env, поведение в БД:

```python
# --- daily song behavior (env, требует рестарта при изменении) ---
song_enabled: bool = True
song_tz: str = "Europe/Moscow"
song_cron: str = "0 21 * * *"
song_min_messages: int = 20
song_max_messages: int = 2000
song_message_max_len: int = 2000
song_llm_chunk_size: int = 50
song_generation_timeout: int = 600        # сек
song_poll_interval: int = 15              # сек

# --- LLM gateway secret ---
openrouter_api_key: str | None = None

# --- self-hosted Suno (опционально, как backup-провайдер; не нужен для sunoapi.org) ---
suno_api_base: str | None = None          # http://suno-api:3000
```

В будущем (post-MVP) можно мигрировать `song_*` поведенческие параметры
из env в БД — но это не приоритет. `OPENROUTER_API_KEY` тоже можно по
аналогии с Suno держать в БД через `/menu → 🤖 LLM-модели`, но для MVP
оставляем в env.

**ВАЖНО (исправлено в Phase A)**: текущий `app/config.py` использовал
`Field(default=..., alias=...)` без `from pydantic import Field` — это
ломало старт бота. В Phase A добавлен импорт одной строкой; `quiet_hours_*`
поля оставлены как есть.

`.env.example` обновляется новой секцией только с тем, что реально нужно
в env:

```env
# --- Daily song feature ---
SONG_ENABLED=true
SONG_TZ=Europe/Moscow
SONG_CRON=0 21 * * *
SONG_MIN_MESSAGES=20
SONG_MAX_MESSAGES=2000

# OpenRouter (LLM gateway)
OPENROUTER_API_KEY=

# Self-hosted Suno API (gcui-art/suno-api) — опционально, как backup.
# Если используем sunoapi.org (default), эта строка не нужна.
SUNO_API_BASE=
```

Для Suno (sunoapi.org) — никаких env-переменных, всё через `/suno` в боте.

## 8. Зависимости (`requirements.txt`)

Добавляем:
```
httpx==0.27.2
```
Для OpenRouter и Suno HTTP-клиентов. aiogram уже тащит aiohttp, но смешивать стили — больно; httpx даёт нормальные timeout/retry-семантики.

## 9. Тестируемость

Юнит-точки (без сети):
- `services/song_summarizer.py` — мокаем `OpenRouterClient.chat`, проверяем чанкование/JSON-ретраи.
- `services/song_writer.py` — то же.
- `services/song_provider.py::LyricsOnlyProvider` — pure unit.
- `services/chat_messages.py::record_message` — БД-тест на синтетическом aiogram-сообщении (фикстуры из существующих тестов, если они есть; если нет — только smoke-тест).

Тесты не пишем автоматически (правило проекта — тесты только по запросу). В `tasks.md` — задача-плейсхолдер «smoke-тесты, если попросят».

## 10. Деплой

1. Self-hosted Suno — отдельный сервис (вариант A: отдельный Railway-проект из [gcui-art/suno-api](https://github.com/gcui-art/suno-api), вариант B: docker на VPS). Бот ходит к нему по `SUNO_API_BASE`. Куки Suno — переменные окружения этого сервиса, не нашего бота.
2. OpenRouter — только `OPENROUTER_API_KEY` в env бота.
3. `@BotFather` → `/setprivacy` → Disable. Re-invite бот в чаты, где надо собирать историю.
4. Alembic-миграции прогоняются при старте сервиса (`startCommand` в `railway.toml` — уже так).
5. После деплоя: владелец заходит в `/menu → 🤖 LLM-модели`, добавляет 1–2 модели (например `meta-llama/llama-3.3-70b-instruct:free` на обе роли), затем включает «🎵 Песня дня» в нужных чатах.

## 11. Открытые вопросы / риски

- **R1**: Suno self-hosted ломается при ротации куки — нужен мониторинг. Mitigation: автоматический fallback на `LyricsOnlyProvider` (F5.4) + лог в OWNER.
- **R2**: OpenRouter `:free` модели иногда отдают rate-limit / 503. Mitigation: ретраи + возможность держать platno fallback (доп. модель в БД, role=both, ручное переключение).
- **R3**: Пользователи могут возмутиться сбором всей истории. Mitigation: явное предупреждение N1.2 + `/song_purge` + транспарентный пин-пост в чат опционально (post-MVP).
- **R4**: Текущий `Field` без импорта в `config.py` — может уже падать на старте. Если падает — фиксим в фазе 1 как часть конфиг-расширения; если не падает (магия pydantic-settings) — оставляем, но не повторяем стиль.
- **R5**: Aiogram message router order — нужно проверить, что reply-capture на призыв-идею не съедает текст до того, как chat_logger его запишет. Mitigation: `chat_logger` регистрируется одним из последних, и в нём НЕ возвращается early-stop (handler возвращает None, message идёт дальше). Проверяем в фазе 1.
