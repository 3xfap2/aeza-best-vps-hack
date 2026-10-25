"""События биллинга: пополнение, оплата продления, автопродление, смена статуса, сигналы других систем.

Один путь для всех источников: POST /api/v1/source/events (по одному), очередь от сервиса приёма на Go
(пачками, app/inbox.py) и демо-поток (app/demo_stream.py). Событие записывается в данные источника,
попадает в журнал и пересчитывает только затронутого клиента.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from app import db, simulator
from app.demo_stream import AUTHOR as DEMO_AUTHOR


class TopupEvent(BaseModel):
    type: Literal["topup"]
    accountId: int
    amount: float = Field(ge=0.01, le=100_000, description="Сумма пополнения, € (от 1 цента)")


class AutoprolongEvent(BaseModel):
    type: Literal["autoprolong"]
    serviceId: int
    enabled: bool


class StatusEvent(BaseModel):
    type: Literal["service_status"]
    serviceId: int
    status: Literal["active", "suspended", "prolong_wait", "deleted", "blocked"]


class PaymentEvent(BaseModel):
    type: Literal["pay_renewal"]
    serviceId: int


class SignalEvent(BaseModel):
    type: Literal["signal"]
    name: Literal["supportTickets30d", "loadDrop", "downgraded"] = Field(
        description="supportTickets30d — для аккаунта (targetId = id аккаунта); loadDrop, downgraded — для услуги")
    targetId: int
    value: float | bool


SourceEvent = Annotated[TopupEvent | AutoprolongEvent | StatusEvent | PaymentEvent | SignalEvent,
                        Field(discriminator="type")]
event_adapter = TypeAdapter(SourceEvent)


def write(conn, event: SourceEvent, at: datetime) -> str:
    """Записать событие в данные источника (без пересчёта). Возвращает описание для журнала."""
    if isinstance(event, TopupEvent):
        return simulator.topup(conn, event.accountId, round(event.amount * 100))
    if isinstance(event, AutoprolongEvent):
        return simulator.set_autoprolong(conn, event.serviceId, event.enabled)
    if isinstance(event, StatusEvent):
        return simulator.set_status(conn, event.serviceId, event.status, at)
    if isinstance(event, SignalEvent):
        return simulator.set_signal(conn, event.targetId, event.name, event.value)
    return simulator.pay_renewal(conn, event.serviceId, at)


def affected_account(conn, event: SourceEvent) -> int | None:
    """Чей score меняет событие — пересчитывается только этот клиент."""
    if isinstance(event, TopupEvent):
        return event.accountId
    if isinstance(event, SignalEvent):
        return event.targetId if event.name == "supportTickets30d" else db.owner_of(conn, event.targetId)
    return db.owner_of(conn, event.serviceId)


# Все события в одном списке — откуда пришли и чем закончились:
# channel api — POST /api/v1/source/events, demo — живой демо-поток, stream — поток от биллинга через сервис на Go
# (очередь event_inbox: pending — ждёт, applied — применено, rejected — отклонено с причиной в result).
_HISTORY = """
WITH ev AS (
    SELECT 'activity-' || a.id AS id, CASE WHEN a.author = %(demo)s THEN 'demo' ELSE 'api' END AS channel,
           a.payload->>'type' AS type, 'applied' AS status, a.created_at AS received_at, a.created_at AS processed_at,
           a.summary AS result, a.author, NULL::text AS event_id, a.payload
    FROM activity_log a WHERE a.kind = 'event' AND a.payload ? 'type'
    UNION ALL
    SELECT 'inbox-' || i.id, 'stream', i.payload->>'type',
           CASE WHEN i.processed_at IS NULL THEN 'pending' WHEN i.ok THEN 'applied' ELSE 'rejected' END,
           i.received_at, i.processed_at, i.result, 'billing', i.event_id, i.payload
    FROM event_inbox i
)
SELECT *, count(*) OVER () AS total FROM ev
WHERE (%(id)s::text IS NULL OR id = %(id)s) AND (%(channel)s::text IS NULL OR channel = %(channel)s)
  AND (%(status)s::text IS NULL OR status = %(status)s) AND (%(type)s::text IS NULL OR type = %(type)s)
ORDER BY received_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s
"""


def history(conn, *, channel: str | None = None, status: str | None = None, type_: str | None = None,
            event_ref: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """События новые сверху с фильтрами; второе значение — сколько всего подходит под фильтр."""
    rows = conn.execute(_HISTORY, {"demo": DEMO_AUTHOR, "id": event_ref, "channel": channel, "status": status, "type": type_,
                                   "limit": limit, "offset": offset}).fetchall()
    return rows, rows[0]["total"] if rows else 0


def apply(radar, event: SourceEvent, author: str, reason: str) -> tuple[str, int]:
    """Записать событие, отметить в журнале и пересчитать затронутого клиента. Возвращает (описание, версия расчёта).

    Ошибки данных (нет такого клиента, оплатить нечего) — simulator.SimulationError.
    """
    with radar.write_lock:
        at = radar.current.result.as_of
        with radar.pool.connection() as conn:
            summary = write(conn, event, at)
            account = affected_account(conn, event)
            db.log_activity(conn, "event", summary, author, event.model_dump())
        cur = radar.recalculate(reason, accounts={account} if account else None)
    return summary, cur.version
