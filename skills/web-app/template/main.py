"""
Веб-приложение агента: FastAPI + статика, без сборки фронтенда.

Проброс бота снимает префикс /app/<id>/; Uvicorn запускается с --root-path,
поэтому внутри приложения пути работают как в корне. Каждое приложение —
отдельный systemd-юнит с DynamicUser (свой uid, свой StateDirectory).

Меняйте код под задачу владельца; структура остаётся.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI(
    title="Agent Web App",
    description="Веб-приложение, опубликованное агентом через прокси бота.",
)

STATIC_DIR = Path(__file__).parent / "static"


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
        "message": "Замените main.py под свою задачу",
    }
