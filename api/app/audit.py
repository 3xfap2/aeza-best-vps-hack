"""Журнал безопасности (audit_log): кто, что, когда, с какого адреса и в каком запросе.

Действия: login, login.failed, logout, forbidden, user.created, user.updated, user.deleted,
settings.changed, export.emails. Читает только администратор — GET /api/v1/audit.
requestId тот же, что в ответе и в журнале запросов: по нему запись находится в логах.
Пароли, токены и ключи сюда не попадают — только логин и что произошло.
"""
from __future__ import annotations

from fastapi import Request
from psycopg.types.json import Jsonb

from app.observability import request_id


def record(conn, request: Request, action: str, actor: str, target: str | None = None,
           details: dict | None = None) -> None:
    conn.execute("INSERT INTO audit_log (actor, action, target, ip, request_id, details) VALUES (%s, %s, %s, %s, %s, %s)",
                 (actor, action, target, request.client.host if request.client else None, request_id.get(),
                  Jsonb(details or {})))


def write(request: Request, action: str, actor: str, target: str | None = None, details: dict | None = None) -> None:
    """Записать в отдельной транзакции — когда своей транзакции у вызывающего нет (отказ во входе, 403)."""
    with request.app.state.pool.connection() as conn:
        record(conn, request, action, actor, target, details)


def recent(conn, action: str | None, actor: str | None, limit: int) -> list[dict]:
    return conn.execute(
        "SELECT id, at, actor, action, target, ip, request_id, details FROM audit_log "
        "WHERE (%(action)s::text IS NULL OR action = %(action)s) AND (%(actor)s::text IS NULL OR actor = %(actor)s) "
        "ORDER BY id DESC LIMIT %(limit)s", {"action": action, "actor": actor, "limit": limit}).fetchall()
