"""Работа маркетолога с клиентом: отметки («позвонили», «продлил», «ушёл» с причиной) и перенос платежа.

Общий путь для панели (routers/customers.py) и Telegram-бота (/defer). Ошибки — ActionError с HTTP-кодом:
роутер превращает её в ответ, бот — в сообщение. Каждое действие уходит в журнал, в панели обновляется
по SSE и публикуется во внешние системы (CRM) событием customer.action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app import db, simulator
from app.scoring.engine import CustomerResult

FINAL = ("renewed", "churned")   # после этих отметок перезванивать не нужно
DEFER_COOLDOWN_DAYS = 30


class ActionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Deferral:
    until: datetime
    services: list[str]
    before: CustomerResult
    after: CustomerResult
    action: dict


def record(radar, customer_id: int, kind: str, reason: str | None, note: str | None, follow_up_days: int | None,
           author: str) -> dict:
    """Отметить работу с клиентом. Возвращает запись журнала."""
    customer = _customer(radar, customer_id)
    if kind == "churned" and not reason:
        raise ActionError(422, "Для «ушёл» укажите причину")
    follow_up = _follow_up(radar, follow_up_days) if kind not in FINAL else None
    with radar.pool.connection() as conn:
        row = db.add_action(conn, customer_id, kind, reason if kind == "churned" else None, note, author,
                            round(customer.expected_loss), follow_up, customer.score)
        _publish(radar, conn, customer, row)
    radar.broker.publish("action", {"customerId": customer_id, "kind": kind})
    return row


def defer_payment(radar, customer_id: int, days: int, note: str | None, author: str) -> Deferral:
    """Отсрочка оплаты на 1–14 дней, чтобы сервер не удалился; не чаще раза в 30 дней на клиента."""
    with radar.write_lock:
        before = _customer(radar, customer_id)
        with radar.pool.connection() as conn, conn.transaction():
            if db.recent_action(conn, customer_id, "deferred", DEFER_COOLDOWN_DAYS):
                raise ActionError(409, f"Клиенту уже переносили платёж за последние {DEFER_COOLDOWN_DAYS} дней")
            try:
                until, names = simulator.defer_payment(conn, customer_id, days, radar.current.result.as_of)
            except simulator.SimulationError as e:
                raise ActionError(409, str(e)) from e
            text = f"Платёж перенесён на {days} дн.: " + ", ".join(names) + (f". {note}" if note else "")
            row = db.add_action(conn, customer_id, "deferred", None, text, author, round(before.expected_loss),
                                until.date(), before.score)
            db.log_activity(conn, "event", f"Перенос платежа клиенту #{customer_id} на {days} дн.", author,
                            {"customerId": customer_id, "days": days})
            _publish(radar, conn, before, row)
        after = radar.recalculate("event", accounts={customer_id}).result.by_id[customer_id]
    radar.broker.publish("action", {"customerId": customer_id, "kind": "deferred"})
    return Deferral(until, names, before, after, row)


def _customer(radar, customer_id: int) -> CustomerResult:
    customer = radar.current.result.by_id.get(customer_id)
    if customer is None:
        raise ActionError(404, f"Клиент #{customer_id} не найден")
    return customer


def _follow_up(radar, days: int | None) -> date | None:
    """«Перезвонить через N дней» — от даты расчёта: в этот день клиент вернётся в «Сегодня»."""
    return radar.current.result.as_of.date() + timedelta(days=days) if days else None


def _publish(radar, conn, customer: CustomerResult, row: dict) -> None:
    """Отметка — во внешние системы (CRM): кто, что сделал и сколько было под угрозой."""
    radar.integrations.publish("customer.action", {
        "customerId": customer.account.id, "email": customer.account.email, "kind": row["kind"], "reason": row["reason"],
        "note": row["note"], "author": row["author"], "followUp": row["followUp"], "score": customer.score,
        "level": customer.level, "expectedLossEur": round(customer.expected_loss / 100, 2),
    })
