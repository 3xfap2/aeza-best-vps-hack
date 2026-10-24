"""Очередь удержания: кому из клиентов под риском написать или позвонить и когда.

Срок берётся из правил хостинга: услуга, которая ждёт оплаты, удаляется через 5 дней после
окончания срока — вместе с данными, и такой клиент почти наверняка уходит насовсем. Поэтому
очередь сортируется по времени, когда деньги будут потеряны безвозвратно, а внутри срока —
по выгоде контакта: ожидаемо сохраним (реально собираемая потеря × доля удержания) − стоимость звонка.

«Перезвонить в дату» возвращает клиента в «Сегодня», когда наступает дата (по дате расчёта).
Ёмкость: если менеджер успевает calls_per_day звонков, в план дня попадают первые из очереди.
После контакта сравниваем сумму под угрозой в момент первой отметки и сейчас — это совпадение
по времени с данными биллинга, а не доказательство, что сработал именно звонок.
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


def queue(result: EngineResult, last: dict[int, dict], save_rate: float, call_cost: float,
          calls_per_day: int = 0, first: dict[int, dict] | None = None) -> list[dict]:
    """Клиенты высокого и среднего риска со сроком, каналом и выгодой контакта (деньги — в евро)."""
    titles = {f.key: f.title for f in FEATURES}
    today = result.as_of.date()
    first = first or {}
    items = []
    for c in result.customers:
        if c.level not in ("high", "medium"):
            continue
        when, kind, days = deadline(c, result.as_of)
        saved = c.expected_loss_paid / 100 * save_rate / 100
        channel = "call" if saved > call_cost else "email"
        act = last.get(c.account.id)
        status = "new" if not act else "done" if act["kind"] in DONE else "in_progress"
        follow_up = act.get("follow_up") if act and status != "done" else None
        due = follow_up is not None and follow_up <= today
        contact = first.get(c.account.id)
        cause = c.main_cause
        items.append({
            "customerId": c.account.id, "email": c.account.email, "level": c.level, "score": c.score,
            "cause": cause, "causeTitle": titles.get(cause, cause) if cause else None,
            "kind": "involuntary" if cause in INVOLUNTARY else "voluntary",
            "action": c.actions[0] if c.actions else c.top_reason,
            "expectedLoss": round(c.expected_loss / 100, 2), "expectedLossPaid": round(c.expected_loss_paid / 100, 2),
            "deadline": when, "deadlineKind": kind, "daysLeft": round(days, 2),
            "bucket": "today" if due else bucket(kind, days), "followUp": follow_up, "followUpDue": due,
            "channel": channel, "expectedSaved": round(saved, 2),
            "net": round(saved - (call_cost if channel == "call" else 0), 2),
            "lastAction": act["kind"] if act else None, "lastActionAt": act["created_at"] if act else None,
            "lastAuthor": act["author"] if act else None, "status": status, "planned": False,
            "contactAt": contact["created_at"] if contact else None,
            "riskAtContact": round(contact["at_risk"] / 100, 2) if contact and contact["at_risk"] is not None else None,
            "scoreAtContact": contact["score_at"] if contact else None,
        })
    # перезвонить сегодня — первыми: клиенту обещали
    items.sort(key=lambda x: (BUCKETS.index(x["bucket"]), x["status"] == "done", not x["followUpDue"], -x["net"]))
    if calls_per_day > 0:
        for x in [x for x in items if x["channel"] == "call" and x["status"] != "done"][:calls_per_day]:
            x["planned"] = True
    return items


def effect(result: EngineResult, first: dict[int, dict]) -> list[dict]:
    """Что изменилось после первого контакта: сумма под угрозой и score тогда и сейчас (евро)."""
    out = []
    for account_id, contact in first.items():
        c = result.by_id.get(account_id)
        if c is None or contact["at_risk"] is None:
            continue
        before, now = contact["at_risk"] / 100, c.expected_loss / 100
        out.append({
            "customerId": account_id, "email": c.account.email, "contactAt": contact["created_at"],
            "contactBy": contact["author"], "riskBefore": round(before, 2), "riskNow": round(now, 2),
            "scoreBefore": contact["score_at"], "scoreNow": c.score, "levelNow": c.level,
            "reduced": round(max(0.0, before - now), 2),
        })
    return sorted(out, key=lambda x: -x["reduced"])


STRATEGIES = (("plan", "План Радара: срок потери, затем выгода звонка"),
              ("loss", "По сумме потери"),
              ("score", "По риску (score)"))


def compare_strategies(items: list[dict], calls_per_day: int, days: int = 7) -> list[dict]:
    """Тот же ресурс звонков — разный порядок. Каждый день звоним calls_per_day клиентам; деньги клиента
    сохраняются (expectedSaved), только если позвонили до срока, когда они пропадут безвозвратно.

    Сценарий на текущих данных: новые клиенты и события за эти дни не учитываются. Он показывает,
    почему очередь сортируется по сроку: дорогого клиента со сроком через неделю можно набрать завтра,
    а того, чья услуга удалится сегодня, — нет.
    """
    pool = [x for x in items if x["status"] != "done"]
    rank = {x["customerId"]: i for i, x in enumerate(pool)}
    keys = {
        "plan": lambda x: (x["channel"] != "call", rank[x["customerId"]]),   # письмо вместо звонка — в конец
        "loss": lambda x: -x["expectedLoss"],
        "score": lambda x: (-(x["score"] or 0), -x["expectedLossPaid"]),
    }
    out = []
    for key, title in STRATEGIES:
        left = sorted(pool, key=keys[key])
        saved = missed = 0.0
        missed_customers = 0
        for day in range(days):
            alive = [x for x in left if x["deadlineKind"] == "overdue" or x["daysLeft"] >= day]
            gone = len(left) - len(alive)
            missed += sum(x["expectedSaved"] for x in left) - sum(x["expectedSaved"] for x in alive)
            missed_customers += gone
            pick, left = alive[:calls_per_day], alive[calls_per_day:]
            saved += sum(x["expectedSaved"] for x in pick)
        late = [x for x in left if x["deadlineKind"] != "overdue" and x["daysLeft"] < days]
        missed += sum(x["expectedSaved"] for x in late)
        out.append({"key": key, "title": title, "saved": round(saved, 2), "missed": round(missed, 2),
                    "missedCustomers": missed_customers + len(late)})
    return out
