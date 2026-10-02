# ProjectAtlas

Учебный проект на FastAPI: сайт для публикации проектов с регистрацией, входом, защищёнными формами и проверкой владельца при удалении. Обложки сохраняются в локальное хранилище или S3; поддерживаются JPEG, PNG и WebP до 5 МБ. Описание можно улучшить demo-провайдером или Gemini.

## Требования

- Python 3.12 или новее
- `uv`
- Для контейнерного запуска: Docker Desktop с Compose

## Установка и запуск

В Windows PowerShell установи `uv`, скопируй файл настроек и установи зависимости:

```bash
winget install --id=astral-sh.uv -e
Copy-Item .env.example .env
uv sync --frozen --dev
```

Запусти сервер разработки:

```bash
uv run fastapi dev app/main.py
```

Сайт будет доступен по адресу `http://127.0.0.1:8000`. Настройки `.env.example` включают локальное хранилище и demo AI; для Gemini или S3 укажи собственные credentials в `.env`.

## Тесты

Из корня репозитория выполни:

```bash
uv run pytest -q
```

## Docker

Собери production-образ и запусти приложение:

```bash
docker compose up --build
```

Проверка доступности:

```bash
Invoke-RestMethod http://127.0.0.1:8000/health
```

Останови приложение, сохранив данные в named volumes:

```bash
docker compose down
```
