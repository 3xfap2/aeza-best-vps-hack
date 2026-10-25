from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app import events
from app.http import page_links
from app.security import current_user

router = APIRouter(prefix="/api/v1/events", tags=["События"], dependencies=[Depends(current_user)])

Channel = Literal["api", "demo", "stream"]
Status = Literal["pending", "applied", "rejected"]


class EventOut(BaseModel):
    id: str = Field(description="activity-N — событие через API или демо-поток, inbox-N — из потока биллинга (Go)")
    channel: Channel = Field(description="api — POST /source/events, demo — живой демо-поток, stream — поток через сервис на Go")
    type: str | None
    status: Status = Field(description="pending — ждёт в очереди, applied — применено, rejected — отклонено")
    receivedAt: datetime
    processedAt: datetime | None
    result: str | None = Field(description="Что изменилось (или почему отклонено)")
    author: str
    eventId: str | None = Field(description="Ключ защиты от повторов из потока биллинга")
    payload: dict


class EventPage(BaseModel):
    items: list[EventOut]
    total: int
    page: int
    pageSize: int


def _out(r: dict) -> EventOut:
    return EventOut(id=r["id"], channel=r["channel"], type=r["type"], status=r["status"], receivedAt=r["received_at"],
                    processedAt=r["processed_at"], result=r["result"], author=r["author"], eventId=r["event_id"],
                    payload=r["payload"])


@router.get("", response_model=EventPage,
            summary="Куда пришли события биллинга и чем закончились: канал, статус, результат (новые сверху)")
def list_events(request: Request, response: Response, channel: Channel | None = None, status: Status | None = None,
                type: str | None = Query(None, max_length=32, description="topup, pay_renewal, autoprolong, service_status, signal"),
                page: int = Query(1, ge=1), pageSize: int = Query(50, ge=1, le=200)) -> EventPage:
    with request.app.state.pool.connection() as conn:
        rows, total = events.history(conn, channel=channel, status=status, type_=type,
                                     limit=pageSize, offset=(page - 1) * pageSize)
    links = page_links(request, page, pageSize, total)
    if links:
        response.headers["Link"] = links
    return EventPage(items=[_out(r) for r in rows], total=total, page=page, pageSize=pageSize)


@router.get("/{event_id}", response_model=EventOut, summary="Одно событие: откуда пришло, статус и что изменило")
def get_event(event_id: str, request: Request) -> EventOut:
    with request.app.state.pool.connection() as conn:
        rows, _ = events.history(conn, event_ref=event_id, limit=1)
    if not rows:
        raise HTTPException(404, "Событие не найдено")
    return _out(rows[0])
