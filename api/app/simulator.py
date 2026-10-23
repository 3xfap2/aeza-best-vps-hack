"""Имитация системы-источника для демонстрации «живых» данных.

События меняют таблицы источника так, как их поменял бы биллинг. Перемотка времени
применяет правила жизненного цикла из описания кейса:
  * срок истёк, автопродление включено и денег хватает — списание и продление;
  * срок истёк, продлить нечем или автопродление выключено — prolong_wait;
  * prolong_wait дольше 5 дней — deleted.
Все изменения помечаются в журнале как event/simulator, чтобы их не путать с данными источника.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from psycopg import Connection

from app import db
from app.domain import GRACE_PERIOD, TERM_HOURS
from app.scoring.features.base import eur

MONTHS = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}


class SimulationError(ValueError):
    pass


def add_term(value: datetime, term: str) -> datetime:
    """Как в генераторе организаторов: месяцы календарные, день ограничен концом месяца."""
    if term not in MONTHS:
        return value + timedelta(hours=TERM_HOURS[term])
    index = value.year * 12 + value.month - 1 + MONTHS[term]
    year, month = divmod(index, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def _service(conn: Connection, service_id: int) -> dict:
    row = conn.execute('SELECT * FROM services WHERE id = %s FOR UPDATE', (service_id,)).fetchone()
    if not row:
        raise SimulationError(f"Услуга {service_id} не найдена")
    return row


def _account(conn: Connection, account_id: int) -> dict:
    row = conn.execute("SELECT * FROM accounts WHERE id = %s FOR UPDATE", (account_id,)).fetchone()
    if not row:
        raise SimulationError(f"Аккаунт {account_id} не найден")
    return row


def _charge(conn: Connection, account: dict, amount: int) -> bool:
    """Списание: сначала основной баланс, затем бонусы, если они разблокированы."""
    bonus = account["bonusBalance"] if account["bonusState"] == "unlocked" else 0
    if account["balance"] + bonus < amount:
        return False
    from_balance = min(account["balance"], amount)
    from_bonus = amount - from_balance
    account["balance"] -= from_balance
    account["bonusBalance"] -= from_bonus
    conn.execute(
        'UPDATE accounts SET balance = %s, "bonusBalance" = %s WHERE id = %s',
        (account["balance"], account["bonusBalance"], account["id"]),
    )
    return True


def _set_status(conn: Connection, service: dict, status: str, at: datetime, source: str) -> None:
    if service["status"] == status:
        return
    conn.execute("UPDATE services SET status = %s WHERE id = %s", (status, service["id"]))
    db.add_transition(conn, service["id"], service["ownerId"], service["status"], status, at, source)
    service["status"] = status


def _next_tx_id(conn: Connection) -> int:
    return conn.execute("SELECT COALESCE(max(id), 0) + 1 AS id FROM transactions").fetchone()["id"]


def topup(conn: Connection, account_id: int, amount_cents: int) -> str:
    if amount_cents <= 0:
        raise SimulationError("Сумма пополнения должна быть больше нуля")
    account = _account(conn, account_id)
    conn.execute(
        'UPDATE accounts SET balance = balance + %s, "totalReplenished" = "totalReplenished" + %s WHERE id = %s',
        (amount_cents, amount_cents, account_id),
    )
    return f"{account['email']}: пополнение {eur(amount_cents)}"


def set_autoprolong(conn: Connection, service_id: int, enabled: bool) -> str:
    service = _service(conn, service_id)
    conn.execute('UPDATE services SET "autoProlong" = %s WHERE id = %s', (enabled, service_id))
    return f"{service['name']}: автопродление {'включено' if enabled else 'выключено'}"


def set_status(conn: Connection, service_id: int, status: str, at: datetime) -> str:
    service = _service(conn, service_id)
    before = service["status"]
    _set_status(conn, service, status, at, "event")
    return f"{service['name']}: статус {before} → {status}"


def pay_renewal(conn: Connection, service_id: int, at: datetime) -> str:
    """Клиент оплачивает один период: деньги списываются, срок продлевается, услуга снова активна."""
    service = _service(conn, service_id)
    if service["status"] in ("deleted", "blocked"):
        raise SimulationError(f"{service['name']}: удалённую или заблокированную услугу продлить нельзя")
    account = _account(conn, service["ownerId"])
    if not _charge(conn, account, service["price"]):
        raise SimulationError(f"{account['email']}: на счёте не хватает {eur(service['price'])} для продления")
    new_expiry = add_term(max(service["expiresAt"], at) if service["status"] == "prolong_wait" else service["expiresAt"],
                          service["paymentTerm"])
    conn.execute('UPDATE services SET "expiresAt" = %s WHERE id = %s', (new_expiry, service_id))
    if service["status"] == "prolong_wait":
        _set_status(conn, service, "active", at, "event")
    conn.execute(
        'INSERT INTO transactions (id, amount, status, "performedAt", "createdAt", type, "serviceId") '
        "VALUES (%s, %s, 'performed', %s, %s, 'prolong', %s)",
        (_next_tx_id(conn), min(service["price"], 1000), at, at, service_id),
    )
    return f"{service['name']}: оплачено продление {eur(service['price'])}, новый срок {new_expiry:%d.%m.%Y %H:%M}"


def advance(conn: Connection, since: datetime, until: datetime) -> list[str]:
    """Прогнать жизненный цикл живых услуг на промежутке (since, until].

    Что просрочено ещё до since (данные источника противоречивы), не пересчитывается
    задним числом: такие события происходят в момент since.
    """
    notes: list[str] = []
    services = list(conn.execute(
        "SELECT * FROM services WHERE status IN ('active', 'suspended', 'prolong_wait') ORDER BY \"expiresAt\" FOR UPDATE"
    ))
    accounts = {r["id"]: r for r in conn.execute("SELECT * FROM accounts FOR UPDATE")}
    for s in services:
        account = accounts[s["ownerId"]]
        if s["status"] in ("active", "suspended"):
            expiry = max(s["expiresAt"], since)
            renewed = 0
            while expiry <= until and s["autoProlong"] and _charge(conn, account, s["price"]):
                expiry = add_term(expiry, s["paymentTerm"])
                renewed += 1
            if renewed:
                s["expiresAt"] = expiry
                conn.execute('UPDATE services SET "expiresAt" = %s WHERE id = %s', (expiry, s["id"]))
                notes.append(f"{s['name']}: автопродление ×{renewed}, списано {eur(renewed * s['price'])}")
            if expiry <= until:
                _set_status(conn, s, "prolong_wait", expiry, "simulator")
                s["waitFrom"] = expiry
                notes.append(f"{s['name']}: срок истёк → ждёт оплаты")
        if s["status"] == "prolong_wait":
            deadline = max(s.get("waitFrom", s["expiresAt"]) + GRACE_PERIOD, since)
            if deadline <= until:
                _set_status(conn, s, "deleted", deadline, "simulator")
                notes.append(f"{s['name']}: 5 дней без оплаты → удалена")
    return notes
