from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi

from app import db
from app.config import load_config
from app.routers import analytics, auth, customers, integrations, metrics, model, oidc, source, stream
from app.scoring.settings import default_settings
from app.security import LoginLimiter, hash_password
from app.source import SnapshotIn
from app.state import AppState
from app.telegram import TelegramBot

log = logging.getLogger("radar")

DESCRIPTION = """
Анализатор рисков оттока: кому из клиентов нужно внимание, почему и сколько денег бизнес может потерять.

**Доступ.** Все эндпоинты с данными требуют входа. Получите токен через `POST /api/v1/auth/login`
и нажмите **Authorize** (Bearer). `/metrics` принимает также токен сборщика `METRICS_TOKEN`.

**Деньги.** Во входном снимке — евроценты (как в источнике), во всех ответах — евро.
"""


def bootstrap(app_state: AppState, config) -> None:
    with app_state.pool.connection() as conn:
        db.upsert_password_user(conn, config.admin_login, hash_password(config.admin_password), "Администратор")
        if db.current_settings(conn) is None:
            db.save_settings(conn, default_settings(), ["Начальные настройки модели"], "system")
        empty = db.source_is_empty(conn)
    if empty and config.seed_path and config.seed_path.exists():
        snap = SnapshotIn.model_validate(json.loads(config.seed_path.read_text(encoding="utf-8")))
        with app_state.pool.connection() as conn:
            counts = db.replace_snapshot(conn, snap, "system", datetime.now(timezone.utc))
            db.log_activity(conn, "snapshot", f"Загружены данные кейса: {counts['accounts']} аккаунтов, "
                            f"{counts['services']} услуг, {counts['transactions']} транзакций", "system", counts)
        log.info("Загружен стартовый снимок %s", config.seed_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    pool = db.create_pool(config.database_url)
    applied = db.migrate(pool)
    if applied:
        log.info("Применены миграции: %s", ", ".join(applied))
    radar = AppState(pool)
    radar.broker.bind(asyncio.get_running_loop())
    bootstrap(radar, config)
    radar.recalculate("startup")

    app.state.config = config
    app.state.pool = pool
    app.state.radar = radar
    app.state.login_limiter = LoginLimiter()
    app.state.metrics_registry = metrics.build_registry(radar)
    bot = None
    if config.telegram_token and config.telegram_link_code:
        bot = TelegramBot(config.telegram_token, config.telegram_link_code, radar, config.public_url)
        bot.start()
    app.state.bot = bot
    try:
        yield
    finally:
        if bot:
            bot.stop()
        pool.close()


app = FastAPI(
    title="Радар оттока API",
    version="1.0.0",
    description=DESCRIPTION,
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)

for r in (auth.router, oidc.router, customers.router, analytics.router, model.router, source.router, stream.router, integrations.router, metrics.router):
    app.include_router(r)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Защита от CSRF: браузер всегда шлёт Origin с изменяющими запросами; чужой сайт — отказ.
    # Клиенты API (curl, биллинг) Origin не шлют и проходят по Bearer-токену.
    origin = request.headers.get("origin")
    if request.method in UNSAFE_METHODS and origin and origin != "null":
        allowed = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", ""),
                   urlsplit(request.app.state.config.public_url).netloc}
        if urlsplit(origin).netloc not in allowed:
            return JSONResponse({"detail": "Запрос с чужого сайта отклонён"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v1/docs"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http", "scheme": "bearer", "description": "Токен из POST /api/v1/auth/login",
    }
    for path, ops in schema["paths"].items():
        if path == "/api/v1/auth/login" or path.startswith("/api/v1/auth/oidc"):
            continue
        for op in ops.values():
            op.setdefault("security", [{"bearerAuth": []}])
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
