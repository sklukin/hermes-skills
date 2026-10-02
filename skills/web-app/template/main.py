"""
Веб-приложение агента: FastAPI + статика, без сборки фронтенда.

Проброс бота снимает префикс /app/<id>/; Uvicorn запускается с --root-path,
поэтому внутри приложения пути работают как в корне. Каждое приложение —
отдельный systemd-юнит с DynamicUser (свой uid, свой StateDirectory).

Меняйте код под задачу владельца; структура остаётся.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI(
    title="Agent Web App",
    description="Веб-приложение, опубликованное агентом через прокси бота.",
)

STATIC_DIR = Path(__file__).parent / "static"

# Персистентные данные: StateDirectory systemd (переживает рестарт юнита).
# /tmp внутри юнита приватный (PrivateTmp) и очищается при остановке — данные
# писать только сюда. Путь передаётся юнитом через Environment=APP_DATA_DIR.
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "/var/lib/agent-apps/0"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/app-health")
async def health() -> dict:
    """Health-проверка: бот ждёт этот ответ после старта юнита."""
    return {"status": "ok"}


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    """Главная страница."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/info")
async def info() -> dict:
    """Пример API: замените на свои данные."""
    return {
        "app": "Agent Web App",
        "version": "1.0.0",
        "data_dir": str(DATA_DIR),
        "message": "Замените main.py под свою задачу",
    }


@app.post("/api/save")
async def save(data: dict) -> dict:
    """Пример записи персистентных данных: файл в APP_DATA_DIR."""
    import json
    (DATA_DIR / "data.json").write_text(json.dumps(data, ensure_ascii=False))
    return {"saved": True, "path": str(DATA_DIR / "data.json")}


@app.get("/api/load")
async def load() -> dict:
    """Пример чтения персистентных данных."""
    import json
    f = DATA_DIR / "data.json"
    if f.exists():
        return json.loads(f.read_text())
    return {"empty": True}
