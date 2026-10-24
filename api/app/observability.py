"""Наблюдаемость: X-Request-ID у каждого запроса и журнал сервиса одной JSON-строкой.

- X-Request-ID берётся из запроса (если прислал nginx или клиент) или создаётся; возвращается в ответе,
  попадает в каждую строку журнала и в тело ошибки problem+json — по нему запрос находится в логах.
- Журнал — JSON в stdout: время, уровень, сообщение, requestId и поля события
  (у строки запроса — метод, путь, статус, длительность). Его читают docker logs, Loki, ELK без парсинга.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import FastAPI, Request

request_id: ContextVar[str] = ContextVar("request_id", default="-")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}
log = logging.getLogger("radar.http")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                 "level": record.levelname.lower(), "logger": record.name, "msg": record.getMessage(),
                 "requestId": request_id.get()}
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("radar")
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def with_request_id(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            if request.url.path not in ("/health", "/health/live", "/metrics"):   # пробы и сборщик не засоряют журнал
                log.info("request", extra={"method": request.method, "path": request.url.path,
                                           "status": response.status_code,
                                           "ms": round((time.perf_counter() - started) * 1000, 1)})
            return response
        finally:
            request_id.reset(token)
