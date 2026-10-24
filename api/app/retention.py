"""Очередь удержания: кому из клиентов под риском написать или позвонить и когда.

Срок берётся из правил хостинга: услуга, которая ждёт оплаты, удаляется через 5 дней после
окончания срока — вместе с данными, и такой клиент почти наверняка уходит насовсем. Поэтому
очередь сортируется по времени, когда деньги будут потеряны безвозвратно, а внутри срока —
по выгоде контакта: ожидаемо сохраним (реально собираемая потеря × доля удержания) − стоимость звонка.
"""
from __future__ import annotations

from datetime import datetime

from app.domain import GRACE_PERIOD
from app.scoring.engine import INVOLUNTARY, CustomerResult, EngineResult
from app.scoring.features import FEATURES

BUCKETS = ("today", "week", "later")
DONE = frozenset({"renewed", "churned"})


def deadline(c: CustomerResult, as_of: datetime) -> tuple[datetime, str, float]:
    """Ближайший срок по услугам под риском: (дата, вид, дней до неё).

    deletion — услуга ждёт оплаты и удалится; overdue — удаление уже задержано (данные расходятся);
    renewal — дата продления, после которой услуга перейдёт в ожидание оплаты.
    """
    best: tuple[datetime, str, float] | None = None
    for r in c.services:
        if r.score is None or r.probability <= 0:
            continue
        s = r.service
        if s.status == "prolong_wait":
            when = s.expiresAt + GRACE_PERIOD
            kind = "overdue" if when < as_of else "deletion"
        else:
            when, kind = s.expiresAt, "renewal"
        days = max(0.0, (when - as_of).total_seconds() / 86400)
        if best is None or (kind == "overdue", -days) > (best[1] == "overdue", -best[2]):
            best = (when, kind, days)
    if best is None:  # риск без срока (например, только неоплаченные счета): ближайшая дата по живым услугам
        s = min((r.service for r in c.services if r.score is not None), key=lambda s: s.expiresAt)
        best = (s.expiresAt, "renewal", max(0.0, (s.expiresAt - as_of).total_seconds() / 86400))
    return best


def bucket(kind: str, days: float) -> str:
    if kind == "overdue" or days <= 1:
        return "today"
    return "week" if days <= 7 else "later"


def queue(result: EngineResult, last: dict[int, dict], save_rate: float, call_cost: float) -> list[dict]:
    """Клиенты высокого и среднего риска со сроком, каналом и выгодой контакта (деньги — в евро)."""
    titles = {f.key: f.title for f in FEATURES}
    items = []
    for c in result.customers:
        if c.level not in ("high", "medium"):
            continue
        when, kind, days = deadline(c, result.as_of)
        saved = c.expected_loss_paid / 100 * save_rate / 100
        channel = "call" if saved > call_cost else "email"
        act = last.get(c.account.id)
        cause = c.main_cause
        items.append({
            "customerId": c.account.id, "email": c.account.email, "level": c.level, "score": c.score,
            "cause": cause, "causeTitle": titles.get(cause, cause) if cause else None,
            "kind": "involuntary" if cause in INVOLUNTARY else "voluntary",
            "action": c.actions[0] if c.actions else c.top_reason,
            "expectedLoss": round(c.expected_loss / 100, 2), "expectedLossPaid": round(c.expected_loss_paid / 100, 2),
            "deadline": when, "deadlineKind": kind, "daysLeft": round(days, 2), "bucket": bucket(kind, days),
            "channel": channel, "expectedSaved": round(saved, 2),
            "net": round(saved - (call_cost if channel == "call" else 0), 2),
            "lastAction": act["kind"] if act else None, "lastActionAt": act["created_at"] if act else None,
            "lastAuthor": act["author"] if act else None,
            "status": "new" if not act else "done" if act["kind"] in DONE else "in_progress",
        })
    items.sort(key=lambda x: (BUCKETS.index(x["bucket"]), x["status"] == "done", -x["net"]))
    return items
