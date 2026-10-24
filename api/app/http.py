"""HTTP-соглашения API — в одном месте.

- Ошибки — application/problem+json (RFC 9457): type, title, status, detail, instance;
  ошибки проверки входных данных дополнительно несут errors: [{loc, msg, type}].
- GET-ответы несут ETag (хеш тела); повторный запрос с If-None-Match получает 304 без тела (RFC 9110).
- Создание ресурса — 201 и Location (см. created()).
- Оптимистичная блокировка — ETag ресурса и If-Match: не совпал — 412 (см. check_if_match()).
"""
from __future__ import annotations

import hashlib
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

PROBLEM = "application/problem+json"


def problem(request: Request, status: int, detail: str, errors: list[dict] | None = None,
            headers: dict | None = None) -> JSONResponse:
    body = {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status, "detail": detail,
            "instance": request.url.path}
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM, headers=headers)


def _validation_errors(raw: list) -> list[dict]:
    return [{"loc": [str(x) for x in e.get("loc", ()) if x != "body"], "msg": str(e.get("msg", "")).removeprefix("Value error, "),
             "type": e.get("type", "")} for e in raw]


def _as_text(errors: list[dict]) -> str:
    return "; ".join((".".join(e["loc"]) + ": " if e["loc"] else "") + e["msg"] for e in errors)


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, list):   # своя 422 со списком полей (например, настройки модели)
            errors = _validation_errors(exc.detail)
            return problem(request, exc.status_code, _as_text(errors), errors, exc.headers)
        return problem(request, exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = _validation_errors(exc.errors())
        return problem(request, 422, _as_text(errors), errors)

    @app.middleware("http")
    async def etag(request: Request, call_next):
        response = await call_next(request)
        if (request.method != "GET" or response.status_code != 200 or not request.url.path.startswith("/api/")
                or response.headers.get("content-type", "").startswith("text/event-stream")):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        # свой ETag ресурса (например, версия настроек) главнее хеша тела
        tag = response.headers.get("etag") or 'W/"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "etag")}
        headers["ETag"] = tag
        if tag in (request.headers.get("if-none-match") or ""):
            return Response(status_code=304, headers={"ETag": tag, "Cache-Control": headers.get("cache-control", "no-store")})
        return Response(body, status_code=200, headers=headers, media_type=response.media_type)


def created(response: Response, location: str) -> None:
    """201 Created: адрес созданного ресурса — в Location."""
    response.headers["Location"] = location


def check_if_match(request: Request, current_etag: str) -> None:
    """If-Match не совпал с текущей версией ресурса — 412: ресурс уже изменили, перечитайте и повторите."""
    expected = request.headers.get("if-match")
    if expected and expected.strip() not in ("*", current_etag):
        raise HTTPException(412, f"Ресурс уже изменили (текущая версия {current_etag}). Перечитайте и повторите.")


def page_links(request: Request, page: int, page_size: int, total: int) -> str:
    """Заголовок Link (RFC 8288) для постраничного списка: first, prev, next, last."""
    last = max(1, -(-total // page_size))
    rels = {"first": 1, "prev": page - 1 if page > 1 else None, "next": page + 1 if page < last else None, "last": last}
    return ", ".join(f'<{request.url.include_query_params(page=p)}>; rel="{rel}"' for rel, p in rels.items() if p) if last > 1 else ""
