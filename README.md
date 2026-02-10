# Telegram DataMatrix Bot (aiogram)

Бот принимает фото/изображение с DataMatrix, принимает обращения по изделиям,
активирует расширенную гарантию и хранит историю заявок.

## Запуск в Docker

1. Создайте файл `.env` рядом с `docker-compose.yml`:
   - Скопируйте `env.example` в `.env`
   - Впишите свой токен Telegram бота в `BOT_TOKEN`
   - Укажите `DB_PATH=/app/data/data.db` для хранения БД в volume
2. Запустите:
   - `docker compose up --build`

## Локальный запуск (без Docker)

1. `python -m venv .venv`
2. `pip install -r requirements.txt`
3. `set BOT_TOKEN=...` (Windows PowerShell) или `export BOT_TOKEN=...`
4. `python -m app.main`

Переменные окружения:
- `BOT_TOKEN` — токен Telegram бота.
- `OUR_CODES` — список "наших" кодов через запятую, бот добавит
  `Код наш`/`Код не наш` в ответ при распознавании.
- `ADMIN_CHAT_IDS` — Telegram ID админов через запятую для уведомлений и статусов.
- `DB_PATH` — путь к SQLite базе.
- `CATALOG_URL`, `WB_URL`, `TG_CHANNEL_URL`, `CERTS_URL`, `FAQ_URL` — ссылки для меню.

Админ-команда:
- `/comment <claim_id> <текст>` — добавить комментарий к заявке и отправить пользователю.

