"""Общий лимит запросов к API: 429 + Retry-After и заголовки RateLimit (черновик IETF httpapi-ratelimit-headers).

- лимит на вызывающего: токен или сессия, без входа — IP; по умолчанию 600 запросов в минуту (RATE_LIMIT_PER_MINUTE);
- каждый ответ API несёт RateLimit-Policy: "default";q=600;w=60 и RateLimit: "default";r=<осталось>;t=<секунд до сброса>;
- сверх лимита — 429 problem+json с Retry-After, запрос не выполняется;
- поток SSE и Swagger не ограничиваются, /metrics и /health — вне /api.
Окно фиксированное и живёт в памяти процесса, как и расчёт (ADR-0003: один экземпляр API).
Вход по паролю ограничен отдельно и строже (LoginLimiter).
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

from fastapi import FastAPI, Request

from app.http import problem

WINDOW = 60
EXEMPT = ("/api/v1/stream", "/api/v1/docs")


class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.time) -> None:
        self.quota = per_minute
        self.clock = clock
        self._windows: dict[str, tuple[int, int]] = {}   # вызывающий → (начало окна, запросов в окне)

    def hit(self, caller: str) -> tuple[int, int]:
        """Учесть запрос. Возвращает (сколько осталось, секунд до сброса); осталось < 0 — лимит превышен."""
        now = int(self.clock())
        start = now - now % WINDOW
        if len(self._windows) > 10_000:   # окна прошлых минут не копятся
            self._windows = {k: v for k, v in self._windows.items() if v[0] == start}
        begun, count = self._windows.get(caller, (start, 0))
        count = count + 1 if begun == start else 1
        self._windows[caller] = (start, count)
        return self.quota - count, start + WINDOW - now


def _caller(request: Request) -> str:
    """Кто вызывает: хеш токена или cookie сессии (сам токен не храним), без входа — IP."""
    token = request.headers.get("authorization") or request.cookies.get("radar_session")
    if token:
        return "token:" + hashlib.sha256(token.encode()).hexdigest()[:32]
    return "ip:" + (request.client.host if request.client else "-")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
        path = request.url.path
        if limiter is None or not path.startswith("/api/") or path.startswith(EXEMPT):
            return await call_next(request)
        remaining, reset = limiter.hit(_caller(request))
        headers = {"RateLimit-Policy": f'"default";q={limiter.quota};w={WINDOW}',
                   "RateLimit": f'"default";r={max(remaining, 0)};t={reset}'}
        if remaining < 0:
            return problem(request, 429, f"Слишком много запросов: не больше {limiter.quota} в минуту. "
                           f"Повторите через {reset} с", headers={**headers, "Retry-After": str(reset)})
        response = await call_next(request)
        response.headers.update(headers)
        return response
