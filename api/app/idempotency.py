"""Idempotency-Key для POST: безопасный повтор запроса (как у Stripe, черновик IETF httpapi-idempotency-key).

Клиент (биллинг, CRM, скрипт) передаёт заголовок Idempotency-Key — любую уникальную строку до 200 символов.
- первый запрос выполняется, его ответ сохраняется на сутки;
- повтор с тем же ключом и тем же телом получает сохранённый ответ с заголовком Idempotent-Replayed: true
  и второй раз не выполняется (сеть оборвалась после пополнения — деньги не зачислятся дважды);
- тот же ключ с другим телом — 422; пока первый запрос с этим ключом выполняется — 409.
Ключи разделены по вызывающему: у двух пользователей одинаковые ключи не пересекаются.
Без заголовка всё работает как обычно. Ответы 5xx не сохраняются — такой запрос можно повторить.
"""
from __future__ import annotations

import hashlib

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from app.http import problem

HEADER = "idempotency-key"
TTL = "24 hours"


def _scope(request: Request) -> str:
    """Кто вызывает: хеш токена или cookie сессии (сам токен не храним)."""
    token = request.headers.get("authorization") or request.cookies.get("radar_session") or "anonymous"
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _claim(pool, scope: str, key: str, request_hash: str) -> dict | None:
    """Занять ключ. None — ключ новый, запрос выполняем; иначе — сохранённая запись."""
    with pool.connection() as conn:
        conn.execute("DELETE FROM idempotency_keys WHERE created_at < now() - %s::interval", (TTL,))
        inserted = conn.execute(
            "INSERT INTO idempotency_keys (scope, key, request_hash) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING key",
            (scope, key, request_hash)).fetchone()
        if inserted:
            return None
        return conn.execute("SELECT request_hash, status_code, response_body, content_type FROM idempotency_keys "
                            "WHERE scope = %s AND key = %s", (scope, key)).fetchone()


def _save(pool, scope: str, key: str, status: int, body: bytes, content_type: str | None) -> None:
    with pool.connection() as conn:
        if status >= 500:   # сбой сервера — освободить ключ, клиент повторит
            conn.execute("DELETE FROM idempotency_keys WHERE scope = %s AND key = %s", (scope, key))
        else:
            conn.execute("UPDATE idempotency_keys SET status_code = %s, response_body = %s, content_type = %s "
                         "WHERE scope = %s AND key = %s", (status, body, content_type, scope, key))


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def idempotency(request: Request, call_next):
        key = request.headers.get(HEADER)
        if request.method != "POST" or not key or not request.url.path.startswith("/api/"):
            return await call_next(request)
        if len(key) > 200:
            return problem(request, 400, "Idempotency-Key — не длиннее 200 символов")
        pool = request.app.state.pool
        scope = _scope(request)
        body = await request.body()
        request_hash = hashlib.sha256(request.url.path.encode() + b"\n" + body).hexdigest()
        saved = await run_in_threadpool(_claim, pool, scope, key, request_hash)
        if saved is not None:
            if saved["request_hash"] != request_hash:
                return problem(request, 422, "Этот Idempotency-Key уже использован для другого запроса")
            if saved["status_code"] is None:
                return problem(request, 409, "Запрос с этим Idempotency-Key ещё выполняется — повторите позже")
            return Response(bytes(saved["response_body"]), status_code=saved["status_code"],
                            media_type=saved["content_type"], headers={"Idempotent-Replayed": "true"})
        response = await call_next(request)
        content = b"".join([chunk async for chunk in response.body_iterator])
        await run_in_threadpool(_save, pool, scope, key, response.status_code, content,
                                response.headers.get("content-type"))
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content, status_code=response.status_code, headers=headers, media_type=response.media_type)
