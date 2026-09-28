# ProjectAtlas

Итоговый проект курса: настоящий сайт на FastAPI с аккаунтами, проектами, AI и облачными обложками.

```bash
cp .env.example .env
uv sync --frozen --dev
uv run fastapi dev app/main.py
```

Интерфейс уже подготовлен: сосредоточься на серверной логике. Финальная проверка — `uv run pytest` и `docker compose up --build`.
