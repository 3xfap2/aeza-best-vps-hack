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


class TopupEvent(BaseModel):
    type: Literal["topup"]
    accountId: int
    amount: float = Field(gt=0, le=100_000, description="Сумма пополнения, €")


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
