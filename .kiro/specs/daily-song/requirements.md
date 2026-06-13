# Daily Song — requirements

## Цель

Каждый день для опт-инутого чата бот собирает сообщения за день, прогоняет их через LLM-сводку и на её основе генерирует короткую песню (mp3 + текст) через Suno API. Результат публикуется в чат.

Фича — отдельный модуль `daily-song`, ложится поверх существующего ideabottg, не ломает прокидывание идей и голосование.

## Глоссарий

- **Окно дня** — диапазон сообщений за «сегодня» в часовом поясе фичи (по умолчанию `Europe/Moscow`). Дефолтно `00:00:00 → 21:00:00` той же даты.
- **Дайджест** — текстовая выжимка сообщений окна дня, сделанная LLM (темы, мемы, цитаты, активные участники).
- **SongDraft** — структурированный результат «songwriter-шага» LLM: `{title, lyrics, style, tags}`.
- **Song-провайдер** — реализация генерации mp3 по `SongDraft`. По умолчанию — self-hosted [gcui-art/suno-api](https://github.com/gcui-art/suno-api), пристёгнутый к Suno-аккаунту владельца.
- **LLM-роль** — `summarizer` (мап-редьюс по сообщениям) или `songwriter` (дайджест → SongDraft). Каждой роли назначена ровно одна активная модель.
- **Песенный job** — APScheduler-job с id `song:{chat_id}`, запускается по cron в TZ фичи.

## Функциональные требования (EARS)

### F1. Захват истории чата

- **F1.1** WHEN бот получает текстовое сообщение в группе/супергруппе, в которой `chats.song_enabled = true`, THEN он SHALL сохранить сообщение в таблицу `chat_messages` с полями `chat_id, tg_message_id, from_user_id, from_username, text, created_at` (UTC).
- **F1.2** WHEN сообщение является командой бота (`/...`), реплаем на призыв-идею или системным сервис-сообщением, THEN бот SHALL **не** сохранять его в `chat_messages`.
- **F1.3** WHEN длина текста сообщения превышает `SONG_MESSAGE_MAX_LEN` (по умолчанию 2000 символов), THEN бот SHALL обрезать текст до лимита перед сохранением.
- **F1.4** WHEN `chats.song_enabled = false`, THEN бот SHALL **не** писать сообщения чата в `chat_messages`.
- **F1.5** История ДО включения `song_enabled` НЕ восстанавливается — Bot API не позволяет читать прошлое. Это явно отмечается в UI при включении фичи.

### F2. Опт-ин и расписание

- **F2.1** Поле `chats.song_enabled: bool` (default `false`) — опт-ин на фичу для конкретного чата.
- **F2.2** WHEN админ переключает «🎵 Песня дня» в `/menu` для чата, THEN бот SHALL обновить `song_enabled`, синхронизировать APScheduler (добавить/убрать job `song:{chat_id}`) и показать предупреждение «история собирается ТОЛЬКО с этого момента».
- **F2.3** Расписание — фиксированное cron-выражение `SONG_CRON` в TZ `SONG_TZ` (по умолчанию `0 21 * * *` в `Europe/Moscow`). Кастомизация per-chat — out of scope для MVP.
- **F2.4** WHEN `chats.is_active = false`, THEN песенный job этого чата SHALL быть удалён из APScheduler.

### F3. Окно дня

- **F3.1** WHEN песенный job срабатывает в момент `T` (TZ = `SONG_TZ`), THEN окно дня SHALL быть `[T_date 00:00:00, T)` в TZ `SONG_TZ`, конвертированное в UTC при выборке из БД.
- **F3.2** WHEN количество сообщений в окне `< SONG_MIN_MESSAGES` (default 20), THEN бот SHALL пропустить день молча: не вызывать LLM, не вызывать Suno, ничего не постить в чат, записать в `daily_songs` строку `status = "skipped"` с `error = "below_threshold:{N}"`.
- **F3.3** WHEN количество сообщений в окне `> SONG_MAX_MESSAGES` (default 2000), THEN бот SHALL взять только последние `SONG_MAX_MESSAGES` штук по `created_at desc` (более свежие — приоритет).
- **F3.4** Дедуп: на пару `(chat_id, date_msk)` SHALL существовать **не более одной** строки в `daily_songs`. Повторный запуск в тот же день — no-op (если status `done`/`generating`) или ретрай (если `failed`).

### F4. LLM-пайплайн (map-reduce)

- **F4.1** LLM-провайдер — **OpenRouter** (`https://openrouter.ai/api/v1`, OpenAI-совместимый endpoint). Ключ — env `OPENROUTER_API_KEY`.
- **F4.2** Модели хранятся в БД (таблица `llm_models`), управляются через бота в рантайме (см. F6). На каждую роль (`summarizer`, `songwriter`) — ровно одна активная модель.
- **F4.3** **Map**: бот SHALL разбить сообщения окна на чанки по `SONG_LLM_CHUNK_SIZE` (default 50 сообщений или ~3000 токенов) и для каждого чанка попросить summarizer-модель вернуть короткую выжимку: темы, мемы, цитаты, активные ники.
- **F4.4** **Reduce**: бот SHALL объединить чанк-выжимки и попросить summarizer-модель вернуть единый дайджест дня (≤1500 токенов): topics, vibe, key_quotes, top_users.
- **F4.5** **Songwriter**: бот SHALL отдать дайджест songwriter-модели и попросить вернуть строгий JSON `{title: str, lyrics: str (≤600 chars), style: str (≤200 chars), tags: list[str]}`. Если ответ — невалидный JSON, бот SHALL сделать до 2 повторов (с инструкцией «верни строго JSON»).
- **F4.6** WHEN любой LLM-вызов падает (timeout, 5xx, rate limit), THEN бот SHALL сделать до 3 ретраев с экспоненциальным бэкоффом. После исчерпания — `status = "failed"`, ошибка в `daily_songs.error`, в чат ничего не публикуется.
- **F4.7** Имена пользователей в дайджесте SHALL быть очищены от персональных данных, кроме username (без user_id, телефонов, email из текста сообщений).

### F5. Song-провайдер

- **F5.1** Абстракция `SongProvider` с методами `submit(SongDraft) -> task_id` и `poll(task_id) -> SongResult|None`. Реализации:
  - **`SunoSelfHostedProvider`** (default) — REST к `SUNO_API_BASE` (gcui-art/suno-api), endpoint `/api/generate`. Self-hosted сервис разворачивается отдельно (Railway или VPS), подключается к Suno-аккаунту владельца через куки.
  - **`SunoApiOrgProvider`** — резерв на платный шлюз. Не реализуется в MVP.
  - **`LyricsOnlyProvider`** — fallback: возвращает «псевдо-результат» с пустым `audio_url`, в чат уходит только текст. Используется, если Suno недоступен.
- **F5.2** Активный провайдер — env `SONG_PROVIDER` ∈ `{self_hosted, lyrics_only}` (default `self_hosted`).
- **F5.3** WHEN `SunoSelfHostedProvider.submit()` возвращает task_id, THEN бот SHALL поллить статус с интервалом 15 сек, таймаут — `SONG_GENERATION_TIMEOUT` (default 600 сек).
- **F5.4** WHEN таймаут истёк или провайдер вернул ошибку, THEN бот SHALL: (а) автоматически переключиться на `LyricsOnlyProvider` для этого запуска, (б) записать `error = "suno_timeout"` (или конкретную ошибку), (в) опубликовать в чат **только текст** песни.
- **F5.5** WHEN провайдер вернул успешный mp3, THEN бот SHALL скачать файл и отправить его в чат как `audio` с подписью (см. F7).

### F6. Управление моделями через бота

- **F6.1** Таблица `llm_models`: `id, slug (unique), display_name, role ('summarizer'|'songwriter'|'both'), is_active (bool), system_prompt (nullable), created_at`.
- **F6.2** Активная модель на роль хранится в существующей таблице `settings` под ключами `llm.active_summarizer` и `llm.active_songwriter` (значение — `slug` модели).
- **F6.3** Команды/UI для админа в личке:
  - **`/menu` → 🎵 Daily Song → 🤖 LLM-модели** — список всех моделей со статусом активности по ролям.
  - Кнопки в карточке модели: «активировать на summarizer», «активировать на songwriter», «изменить системный промпт», «удалить».
  - Кнопка «➕ Добавить модель» → FSM-визард: ввод `slug` (например `meta-llama/llama-3.3-70b-instruct:free`) → ввод `display_name` → выбор `role` → опционально `system_prompt`.
- **F6.4** WHEN админ пытается удалить модель, активную в любой роли, THEN бот SHALL запретить удаление и подсказать сначала переключить роль на другую модель.
- **F6.5** WHEN запускается LLM-вызов на роль, для которой не назначена активная модель, THEN бот SHALL пропустить генерацию песни (`status = "failed"`, `error = "no_active_model:{role}"`) и нотифицировать `OWNER_ID` в личке.
- **F6.6** В UI при добавлении модели бот SHALL показать ссылку-подсказку на каталог OpenRouter (`https://openrouter.ai/models`) и пример бесплатной модели (`:free` суффикс).

### F7. Постинг результата в чат

- **F7.1** Формат сообщения в чат:
  - **audio**: mp3, caption — `🎵 <b>Песня дня</b> · {title}\n\n📊 {N} сообщений за {окно}\n🎨 {style}` (HTML).
  - Следом — отдельное сообщение с полным `lyrics` (если влезает в 4096 символов; иначе — обрезаем до 4000 + «…»).
- **F7.2** WHEN сработал `LyricsOnlyProvider` (нет mp3), THEN бот SHALL отправить только текстовое сообщение с заголовком `🎵 <b>Песня дня (только текст)</b>` + lyrics + ссылкой на suno.com/create со ссылкой-промптом.
- **F7.3** Quiet hours **не** глушат песенную отправку — это запрошенное пользователем 21:00, оно вне окна 23:00→08:00 по умолчанию. Если admin когда-нибудь сместит cron в окно quiet hours, поведение остаётся прежним (фича игнорирует quiet hours).
- **F7.4** WHEN отправка в чат падает (бот выкинут, чат удалён), THEN бот SHALL записать `status = "failed"`, `error = "post_failed:{tg_error}"` и нотифицировать `OWNER_ID`.

### F8. Persistence и idempotency

- **F8.1** Таблица `daily_songs`: `id, chat_id (FK), date_msk (date), status ('queued'|'generating'|'done'|'skipped'|'failed'), provider, provider_task_id, audio_url, title, lyrics, style, n_messages, created_at, finished_at, error`. Уникальный индекс `(chat_id, date_msk)`.
- **F8.2** Все этапы (запуск job, submit в Suno, poll, post) обновляют статус в `daily_songs` транзакционно.
- **F8.3** WHEN бот стартует и видит «зависшую» строку `daily_songs` со `status in ('queued','generating')` старше суток, THEN бот SHALL пометить её `failed` с `error = "stale_on_restart"`.

### F9. Команды для отладки

- **F9.1** **`/song_test <chat_id>`** (только для админов, в ЛС) — прогнать пайплайн на сегодняшнем окне без записи в `daily_songs`, dry-run mode: на каждом этапе постит в ЛС админу промежуточный результат (выжимки чанков, дайджест, SongDraft, mp3-ссылка).
- **F9.2** **`/song_now <chat_id>`** — то же, что и job, но запущенный руками: пишет в `daily_songs`, постит в чат как обычно. Полезно для бэкаппа, если cron пропустил.
- **F9.3** **`/song_stats`** — общая статистика: сколько песен, по чатам, distribution статусов за последние 30 дней.

## Нефункциональные требования

### N1. Privacy mode и UX-предупреждения

- **N1.1** В `@BotFather` для бота privacy mode ДОЛЖЕН быть выключен (`/setprivacy` → Disable). Без этого F1 не работает.
- **N1.2** WHEN админ впервые включает `song_enabled` в чате, THEN бот SHALL показать в `/menu` предупреждение:
  - «Включён сбор истории чата. Бот пишет в БД все текстовые сообщения этого чата с этого момента. Выключи в любой момент тут же.»
- **N1.3** Бот SHALL предоставить команду **`/song_purge <chat_id>`** для удаления всей сохранённой истории чата из `chat_messages` (только OWNER, с подтверждением).
- **N1.4** В будущем (post-MVP): автоматическое удаление сообщений старше `SONG_RETENTION_DAYS` (default 30) — заложить в дизайне, не делать в MVP.

### N2. Лимиты и стоимость

- **N2.1** Suno self-hosted free tier — 50 credits/день ≈ 10 песен/день. Бот SHALL логировать каждый вызов Suno с timestamp, чтобы при превышении лимита было видно по логам.
- **N2.2** OpenRouter — рекомендуем `:free` модели (Llama-3.3-70B free, Gemini-2.0-flash-exp:free и т.п.). Платные модели разрешены, но за бюджет отвечает владелец.
- **N2.3** Один запуск пайплайна на 500–2000 сообщений SHALL укладываться в 5 минут (LLM ≤2 мин, Suno ≤3 мин). Иначе таймаут F5.3.

### N3. Безопасность

- **N3.1** `OPENROUTER_API_KEY`, `SUNO_API_BASE`, любые куки/токены в env — никогда не логировать в открытом виде, маскировать как `***` (по аналогии с `database_url_masked`).
- **N3.2** В чат-ответ бот SHALL **не** включать сырые сообщения чата вне дайджеста. lyrics — это художественная переработка LLM, не дословное цитирование.

### N4. Observability

- **N4.1** Логи каждого этапа пайплайна с `chat_id` и `daily_song_id` в structured-стиле (как сейчас в проекте: `log.info("...", chat_id, ...)`).
- **N4.2** При падении — error с полным traceback в лог, без проброса в чат.

## Out of scope (для MVP)

- Per-chat кастомные cron / окна / жанры.
- Голосовалка «понравилась песня?» (можно использовать существующий voting на `Idea`-ах, но это отдельная задача).
- Картинка-обложка (Suno возвращает image_url — можно прикладывать, но не в первом релизе).
- Автоматическое удаление старых `chat_messages` по retention (заложено в N1.4, реализация позже).
- Платные провайдеры Suno (sunoapi.org и т.п.) — абстракция готова, реализация — позже.
