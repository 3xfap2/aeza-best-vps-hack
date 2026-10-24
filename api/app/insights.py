"""Расчёты для решений маркетолога: «что будет, если…» и календарь продлений.

«Что если» считается на копии данных одного клиента той же моделью и с той же датой
расчёта — исходные данные не меняются.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta

from app.domain import Account, Snapshot
from app.scoring.engine import EngineResult, evaluate
from app.money import fmt_cents
from app.scoring.settings import AsOf, ModelSettings
from app.simulator import add_term


def _client_snapshot(snapshot: Snapshot, account_id: int) -> Snapshot:
    services = [s for s in snapshot.services if s.ownerId == account_id]
    ids = {s.id for s in services}
    return Snapshot(
        accounts=[a for a in snapshot.accounts if a.id == account_id],
        services=services,
        transactions=[t for t in snapshot.transactions if t.serviceId in ids],
        reference_at=snapshot.reference_at,
    )


def what_if(result: EngineResult, snapshot: Snapshot, account_id: int) -> list[dict]:
    """Сценарии удержания для клиента: пополнить счёт, включить автопродление, оплатить, включить сервер."""
    customer = result.by_id.get(account_id)
    if customer is None or customer.score is None:
        return []
    settings: ModelSettings = result.settings.model_copy(update={"asOf": AsOf(mode="fixed", value=result.as_of)})
    base = _client_snapshot(snapshot, account_id)
    account: Account = base.accounts[0]
    live = [s for s in base.services if s.is_live]
    scenarios: list[tuple[str, str, Snapshot]] = []

    deficit = customer.renewal_need - account.available_funds
    if deficit > 0:
        snap = copy.deepcopy(base)
        snap.accounts[0] = replace(account, balance=account.balance + deficit)
        scenarios.append(("topup", f"Пополнит счёт на {fmt_cents(deficit)}", snap))

    no_ap = [s for s in live if not s.autoProlong]
    if no_ap:
        snap = copy.deepcopy(base)
        snap.services = [replace(s, autoProlong=True) if s.id in {x.id for x in no_ap} else s for s in snap.services]
        names = ", ".join(s.name for s in no_ap[:3]) + ("…" if len(no_ap) > 3 else "")
        scenarios.append(("autoprolong", f"Включит автопродление ({names})", snap))

    waiting = [s for s in live if s.status == "prolong_wait"]
    if waiting:
        snap = copy.deepcopy(base)
        cost = sum(s.price for s in waiting)
        snap.services = [replace(s, status="active", expiresAt=add_term(max(s.expiresAt, result.as_of), s.paymentTerm))
                         if s.status == "prolong_wait" else s for s in snap.services]
        snap.accounts[0] = replace(account, balance=account.balance + cost, totalReplenished=account.totalReplenished + cost)
        scenarios.append(("pay", f"Оплатит ожидающие услуги на {fmt_cents(cost)}", snap))

    soon = result.as_of + timedelta(days=7)
    due = [s for s in live if s.status == "prolong_wait" or s.expiresAt <= soon]
    if due:
        snap = copy.deepcopy(base)
        ids = {s.id for s in due}
        until = result.as_of + timedelta(days=7)
        snap.services = [replace(s, status="active", expiresAt=until) if s.id in ids and s.status == "prolong_wait"
                         else replace(s, expiresAt=max(s.expiresAt, result.as_of) + timedelta(days=7)) if s.id in ids else s
                         for s in snap.services]
        scenarios.append(("defer", "Перенесём платёж на 7 дней", snap))

    suspended = [s for s in live if s.status == "suspended"]
    if suspended:
        snap = copy.deepcopy(base)
        snap.services = [replace(s, status="active") if s.status == "suspended" else s for s in snap.services]
        scenarios.append(("resume", "Снова включит сервер", snap))

    out = []
    for key, title, snap in scenarios:
        after = evaluate(snap, settings).by_id[account_id]
        out.append({
            "key": key, "title": title,
            "score": after.score, "level": after.level,
            "expectedLoss": round(after.expected_loss / 100, 2),
            "saved": round((customer.expected_loss - after.expected_loss) / 100, 2),
        })
    # сценарий, который ничего не меняет, маркетологу не нужен
    return sorted((x for x in out if x["saved"] > 0.009), key=lambda x: -x["saved"])


def renewals(result: EngineResult, snapshot: Snapshot, days: int) -> list[dict]:
    """Продления в ближайшие days дней и что им помешает: нет денег или выключено автопродление."""
    as_of = result.as_of
    until = as_of + timedelta(days=days)
    accounts = {a.id: a for a in snapshot.accounts}
    # деньги аккаунта расходуются на продления по порядку дат
    left: dict[int, int] = {a.id: a.available_funds for a in snapshot.accounts}
    items = []
    for s in sorted((s for s in snapshot.services if s.status in ("active", "suspended") and as_of <= s.expiresAt <= until),
                    key=lambda s: s.expiresAt):
        acc = accounts[s.ownerId]
        enough = left[acc.id] >= s.price
        left[acc.id] -= s.price
        problem = None if s.autoProlong and enough else (
            "no_money" if not enough else "no_autoprolong")
        c = result.by_id.get(acc.id)
        items.append({
            "date": s.expiresAt.isoformat(), "customerId": acc.id, "email": acc.email,
            "serviceId": s.id, "service": s.name, "paymentTerm": s.paymentTerm, "price": round(s.price / 100, 2),
            "autoProlong": s.autoProlong, "funds": round(acc.available_funds / 100, 2),
            "problem": problem, "level": c.level if c else None, "score": c.score if c else None,
        })
    return items



def message(result: EngineResult, account_id: int) -> dict | None:
    """Черновик письма или сообщения клиенту по главной причине риска — маркетолог правит и отправляет сам."""
    c = result.by_id.get(account_id)
    if c is None or c.score is None or not c.contributions:
        return None
    top = c.contributions[0]
    svc = next((r.service for r in c.services if r.service.id == top.service_id), None)
    name = svc.name if svc else "ваш сервер"
    deficit = max(0, c.renewal_need - c.account.available_funds)
    due = f"{svc.expiresAt:%d.%m}" if svc else ""
    greet = "Здравствуйте!\n\n"
    sign = "\n\nЕсли нужна помощь — просто ответьте на это письмо.\nКоманда хостинга"
    templates = {
        "prolong_wait": ("Сервер ждёт оплаты", f"Срок оплаты услуги {name} истёк, сейчас она ждёт оплаты. "
                         f"Чтобы сервер и данные сохранились, продлите её в личном кабинете — это займёт минуту."),
        "insufficient_funds": ("Не хватит средств на продление", f"Для продления ваших услуг в ближайшие 30 дней нужно "
                               f"{fmt_cents(c.renewal_need)}, на счёте сейчас {fmt_cents(c.account.available_funds)}. "
                               f"Пополните, пожалуйста, баланс на {fmt_cents(deficit)}" + (f" до {due}" if due else "") + ", чтобы сервер не остановился."),
        "expiring_no_autoprolong": ("Скоро закончится срок услуги", f"Срок услуги {name} заканчивается {due}, а автопродление выключено. "
                                    f"Включите его в личном кабинете — и сервер продлится сам, без перерывов."),
        "no_autoprolong_no_funds": ("Сервер может остановиться", f"У услуги {name} выключено автопродление и на счёте недостаточно средств. "
                                    f"Чтобы сервер не остановился {due}, пополните баланс и включите автопродление."),
        "suspended": ("Ваш сервер выключен", f"Мы заметили, что сервер {name} выключен, хотя услуга оплачивается. "
                      f"Если он больше не нужен, подберём тариф дешевле; если что-то не работает — поможем настроить."),
        "unpaid_transactions": ("Остался неоплаченный счёт", "У вас есть неоплаченный счёт за услуги. Оплатите его в личном кабинете, чтобы избежать перерывов."),
        "partial_churn": ("Как вам у нас?", "Вы недавно отказались от части услуг. Расскажите, что можно сделать лучше, — для оставшихся предложим удобные условия."),
        "support_tickets": ("Всё ли решилось?", "Вы несколько раз обращались в поддержку. Хотим убедиться, что всё решено, — ответьте, если что-то ещё мешает."),
        "usage_drop": ("Сервер почти не используется", f"Нагрузка на {name} заметно снизилась. Если ресурсов с запасом — подберём тариф дешевле, чтобы вы не переплачивали."),
        "plan_downgrade": ("Подобрали ли вы подходящий тариф?", "Вы перешли на тариф попроще. Если не хватает ресурсов или что-то не устраивает — подскажем лучший вариант."),
    }
    subject, body = templates.get(top.feature, ("Ваши услуги", "Хотим убедиться, что у вас всё в порядке с услугами."))
    return {"subject": subject, "text": greet + body + sign, "cause": top.feature, "email": c.account.email,
            "generatedBy": "template"}
