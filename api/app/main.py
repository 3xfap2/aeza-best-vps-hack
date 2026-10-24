from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import load_config
from app import ai, demo_stream
from app.inbox import Inbox
from app.integrations import Publisher, build_sinks
from app.routers.source import snapshot_origin
from app.routers import ai as ai_router
from app.routers import analytics, auth, customers, feedback, fx, integrations, metrics, model, oidc, retention, source, stream, users
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
            kind = "extended" if config.seed_path == config.demo_path else "case" if config.seed_path == config.case_path else "custom"
            counts = db.replace_snapshot(conn, snap, "system", datetime.now(timezone.utc),
                                         origin=snapshot_origin(snap, config.case_path, kind))
            db.log_activity(conn, "snapshot", f"Загружены данные кейса: {counts['accounts']} аккаунтов, "
                            f"{counts['services']} услуг, {counts['transactions']} транзакций", "system", counts)
        log.info("Загружен стартовый снимок %s", config.seed_path)
    with app_state.pool.connection() as conn:   # история к расширенному набору (в том числе после обновления)
        info = db.snapshot_info(conn)
        if info and info["origin"].get("kind") == "extended" and not db.history_info(conn)["snapshots"]:
            db.replace_history(conn, source.demo_history(config), "demo")


async def scheduled_recalc(radar: AppState) -> None:
    """Периодичность прогноза: плановый пересчёт раз в recalcMinutes.

    Изменения данных и настроек пересчитываются сразу; плановый пересчёт нужен, когда время
    идёт само (дата расчёта «сейчас»): сроки приближаются, вероятности растут.
    """
    while True:
        await asyncio.sleep(30)
        try:
            cur = radar.current
            due = (datetime.now(timezone.utc) - cur.computed_at).total_seconds() >= cur.result.settings.recalcMinutes * 60
            if due:
                await asyncio.to_thread(radar.recalculate, "schedule")
        except Exception:  # пересчёт по расписанию не должен ронять сервер
            log.exception("Плановый пересчёт не удался")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    pool = db.create_pool(config.database_url)
    applied = db.migrate(pool)
    if applied:
        log.info("Применены миграции: %s", ", ".join(applied))
    radar = AppState(pool)
    radar.integrations = Publisher(build_sinks(dict(os.environ)))
    radar.broker.bind(asyncio.get_running_loop())
    bootstrap(radar, config)
    radar.recalculate("startup")

    app.state.config = config
    app.state.pool = pool
    app.state.radar = radar
    app.state.login_limiter = LoginLimiter()
    ai_cfg = ai.config_from_env(dict(os.environ))
    with pool.connection() as conn:
        panel = db.get_config(conn, "ai")
    if panel:   # подключение из панели главнее переменных окружения
        ai_cfg = ai.config_from_panel(panel["value"], dict(os.environ))
    app.state.ai = ai.AIClient(ai_cfg) if ai_cfg else None
    app.state.ai_source = "panel" if panel else "env" if ai_cfg else "off"
    app.state.demo_stream = demo_stream.DemoStream(radar, source.demo_sender(app.state))
    if os.environ.get("DEMO_STREAM", "").lower() in ("on", "1", "true"):   # стенд «живёт» сам
        app.state.demo_stream.start(int(os.environ.get("DEMO_STREAM_INTERVAL", "10")), 0)
    if ai_cfg:
        log.info("ИИ-помощник: %s, модель %s", app.state.ai.title, ai_cfg.model)
    app.state.inbox = Inbox(radar, config.database_url)   # события из сервиса приёма (Go) пачками
    app.state.inbox.start()
    app.state.metrics_registry = metrics.build_registry(radar)
    scheduler = asyncio.create_task(scheduled_recalc(radar))
    bot = None
    if config.telegram_token and config.telegram_link_code:
        bot = TelegramBot(config.telegram_token, config.telegram_link_code, radar, config.public_url,
                          config.telegram_digest_hour, app.state.ai)
        bot.start()
    app.state.bot = bot
    try:
        yield
    finally:
        scheduler.cancel()
        app.state.inbox.stop()
        radar.integrations.close()
        app.state.demo_stream.stop()
        if bot:
            bot.stop()
        pool.close()


app = FastAPI(
    title="Радар оттока API",
    version="1.0.0",
    description=DESCRIPTION,
    docs_url=None,  # Swagger отдаём сами — со своими файлами, без внешнего CDN
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)

for r in (auth.router, oidc.router, customers.router, retention.router, analytics.router, model.router, source.router,
          users.router, feedback.router, ai_router.router, fx.router, stream.router, integrations.router, metrics.router):
    app.include_router(r)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")


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
    # JSON API ничего не исполняет; страница Swagger — свои скрипты и стили (плюс её встроенный запуск).
    response.headers.setdefault("Content-Security-Policy", DOCS_CSP if request.url.path.startswith("/api/v1/docs") else API_CSP)
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v1/docs"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


STATIC = Path(__file__).resolve().parent / "static"
app.mount("/api/v1/docs-static", StaticFiles(directory=STATIC / "swagger"), name="swagger-static")


@app.get("/api/v1/docs", include_in_schema=False)
def swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/api/v1/openapi.json", title="Радар оттока API",
        swagger_js_url="/api/v1/docs-static/swagger-ui-bundle.js",
        swagger_css_url="/api/v1/docs-static/swagger-ui.css",
        swagger_favicon_url="/favicon.svg",
        swagger_ui_parameters={"persistAuthorization": True},
    )


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
