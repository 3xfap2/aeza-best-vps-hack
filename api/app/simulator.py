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
from psycopg.types.json import Jsonb

from app import db
from app.domain import GRACE_PERIOD, TERM_HOURS
from app.money import fmt_cents

MONTHS = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}


class SimulationError(ValueError):
    """Событие невозможно на текущих данных; status — HTTP-код ответа API."""
    status = 409


class NotFoundError(SimulationError):
    status = 404


class InvalidInputError(SimulationError):
    status = 422


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
        raise NotFoundError(f"Услуга {service_id} не найдена")
    return row


def _account(conn: Connection, account_id: int) -> dict:
    row = conn.execute("SELECT * FROM accounts WHERE id = %s FOR UPDATE", (account_id,)).fetchone()
    if not row:
        raise NotFoundError(f"Аккаунт {account_id} не найден")
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


SIGNAL_TARGETS = {"supportTickets30d": "account", "loadDrop": "service", "downgraded": "service"}


def set_signal(conn: Connection, target_id: int, name: str, value: float | bool) -> str:
    """Сигнал из другой системы: поддержка, мониторинг, смена тарифа."""
    table = "accounts" if SIGNAL_TARGETS.get(name) == "account" else "services"
    row = conn.execute(f"SELECT * FROM {table} WHERE id = %s FOR UPDATE", (target_id,)).fetchone()
    if not row:
        raise NotFoundError(f"{'Аккаунт' if table == 'accounts' else 'Услуга'} {target_id} не найден")
    conn.execute(f"UPDATE {table} SET signals = signals || jsonb_build_object(%s::text, %s::jsonb) WHERE id = %s",
                 (name, Jsonb(value), target_id))
    who = row["email"] if table == "accounts" else row["name"]
    if name == "loadDrop":
        return f"{who}: мониторинг — нагрузка упала на {float(value):.0%}".replace("%", " %")
    if name == "supportTickets30d":
        return f"{who}: поддержка — обращений за 30 дней: {int(value)}"
    if name == "downgraded":
        return f"{who}: тариф понижен" if value else f"{who}: тариф не понижался"
    return f"{who}: сигнал {name} = {value}"


def topup(conn: Connection, account_id: int, amount_cents: int) -> str:
    if amount_cents <= 0:
        raise InvalidInputError("Сумма пополнения должна быть больше нуля")
    account = _account(conn, account_id)
    conn.execute(
        'UPDATE accounts SET balance = balance + %s, "totalReplenished" = "totalReplenished" + %s WHERE id = %s',
        (amount_cents, amount_cents, account_id),
    )
    return f"{account['email']}: пополнение {fmt_cents(amount_cents)}"


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
        raise SimulationError(f"{account['email']}: на счёте не хватает {fmt_cents(service['price'])} для продления")
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
    return f"{service['name']}: оплачено продление {fmt_cents(service['price'])}, новый срок {new_expiry:%d.%m.%Y %H:%M}"


DEFER_MAX_DAYS = 14
DEFER_SOON_DAYS = 7   # переносим платежи, срок которых наступит в ближайшую неделю или уже прошёл


def defer_payment(conn: Connection, account_id: int, days: int, at: datetime) -> tuple[datetime, list[str]]:
    """Отсрочка платежа: услуги клиента со сроком в ближайшую неделю (или ждущие оплаты) работают ещё days дней.

    Ждущая оплаты услуга снова активна до новой даты — удаление не наступит; у остальных срок сдвигается.
    В продукте это вызов API биллинга; здесь — демонстрационный биллинг на тех же правилах.
    """
    if not 1 <= days <= DEFER_MAX_DAYS:
        raise SimulationError(f"Перенести можно на 1–{DEFER_MAX_DAYS} дней")
    _account(conn, account_id)
    soon = at + timedelta(days=DEFER_SOON_DAYS)
    services = list(conn.execute(
        "SELECT * FROM services WHERE \"ownerId\" = %s AND (status = 'prolong_wait' OR "
        "(status IN ('active', 'suspended') AND \"expiresAt\" <= %s)) ORDER BY id FOR UPDATE", (account_id, soon)))
    if not services:
        raise SimulationError("Переносить нечего: у клиента нет платежей в ближайшую неделю")
    until = at + timedelta(days=days)
    names = []
    for s in services:
        new_expiry = until if s["status"] == "prolong_wait" else max(s["expiresAt"], at) + timedelta(days=days)
        conn.execute('UPDATE services SET "expiresAt" = %s WHERE id = %s', (new_expiry, s["id"]))
        if s["status"] == "prolong_wait":
            _set_status(conn, s, "active", at, "event")
        names.append(f"{s['name']} до {new_expiry:%d.%m}")
    return until, names


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
                notes.append(f"{s['name']}: автопродление ×{renewed}, списано {fmt_cents(renewed * s['price'])}")
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
