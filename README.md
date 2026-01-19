# Telegram DataMatrix Bot (aiogram)

Бот принимает фото/изображение с DataMatrix и возвращает считанное значение.

## Запуск в Docker

1. Создайте файл `.env` рядом с `docker-compose.yml`:
   - Скопируйте `env.example` в `.env`
   - Впишите свой токен Telegram бота в `BOT_TOKEN`
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
  `Код наш`/`Код не наш` в ответ.

## Проверка распознавания на тестовых файлах

В папке `test` можно проверить распознавание без запуска бота:

- `python -m scripts.scan_test`

