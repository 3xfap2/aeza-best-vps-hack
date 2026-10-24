"""Расчёт риска оттока.

Score услуги = min(100, Σ вес признака × сила признака).
Score клиента = максимум по его живым услугам (active, suspended, prolong_wait).
Вероятность непродления услуги = score / 100.
Ожидаемая потеря = Σ выручка услуги за горизонт × вероятность непродления.
Реально собираемая потеря — та же сумма, но выручка клиента не больше того, что он способен заплатить
(деньги на счёте или средний платёж в месяц за время жизни), — см. pay_capacity.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.domain import GRACE_PERIOD, Account, Service, Snapshot, Transaction

from .features import FEATURES, ServiceContext
from app.money import fmt_cents

from .features.base import days_word, humanize_days
from .settings import ModelSettings

LEVELS = ("high", "medium", "low", "no_score", "churned")
LEVEL_TITLES = {
    "high": "Высокий",
    "medium": "Средний",
    "low": "Низкий",
    "no_score": "Нет оценки",
    "churned": "Ушёл",
}
STATUS_TITLES = {
    "active": "Активна",
    "suspended": "Выключена",
    "prolong_wait": "Ждёт оплаты",
    "deleted": "Удалена",
    "blocked": "Заблокирована",
}


@dataclass
class Contribution:
    feature: str
    title: str
    weight: float
    strength: float
    points: float
    reason: str
    service_id: int
    service_name: str


@dataclass
class ServiceResult:
    service: Service
    score: float | None
    probability: float
    revenue_horizon: float      # евроценты, сколько услуга принесёт за горизонт
    expected_loss: float        # евроценты
    days_left: float
    contributions: list[Contribution] = field(default_factory=list)
    quality_issues: list[str] = field(default_factory=list)
    ltv: float = 0.0            # евроценты, выручка за ожидаемый срок жизни


@dataclass
class CustomerResult:
    account: Account
    score: int | None
    level: str
    note: str | None
    top_reason: str | None
    expected_loss: float
    revenue_horizon: float
    renewal_need: int
    ltv_at_risk: float
    services: list[ServiceResult]
    contributions: list[Contribution]
    actions: list[str]
    quality_issues: list[str]
    paid_monthly: float = 0.0           # евроценты, сколько клиент в среднем платит в месяц
    expected_loss_paid: float = 0.0     # евроценты, ожидаемая потеря в пределах платёжеспособности

    @property
    def main_cause(self) -> str | None:
        """Признак с наибольшим вкладом — по нему клиент попадает в кампанию удержания."""
        return self.contributions[0].feature if self.contributions else None


@dataclass
class TimelinePoint:
    date: str
    fact_recorded: int = 0
    fact_restored: int = 0
    forecast: float = 0.0
    fact_recorded_eur: float = 0.0
    fact_restored_eur: float = 0.0
    forecast_eur: float = 0.0


@dataclass
class Summary:
    as_of: datetime
    horizon_days: int
    customers_total: int
    levels: dict[str, int]
    level_loss: dict[str, float]
    expected_loss: float
    worst_case: float
    ltv_at_risk: float
    revenue_at_risk: float
    balance_at_risk: int
    services_at_risk: int
    quality_issues: int
    causes: list[dict]
    expected_loss_paid: float = 0.0


# Недобровольный отток — клиент мог бы остаться, но не прошла оплата; добровольный — решение клиента.
INVOLUNTARY = frozenset({"prolong_wait", "insufficient_funds", "unpaid_transactions"})


@dataclass
class EngineResult:
    as_of: datetime
    settings: ModelSettings
    customers: list[CustomerResult]
    summary: Summary
    by_id: dict[int, CustomerResult]


@dataclass(frozen=True)
class Transition:
    service_id: int
    account_id: int
    from_status: str | None
    to_status: str
    at: datetime


def resolve_as_of(settings: ModelSettings, snapshot: Snapshot, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if settings.asOf.mode == "fixed" and settings.asOf.value:
        return settings.asOf.value
    if settings.asOf.mode == "now":
        return now
    return snapshot.reference_at or snapshot.latest_timestamp() or now


def renewal_cost(service: Service, days_left: float, horizon_days: int) -> int:
    """Сколько денег нужно, чтобы услуга проработала весь горизонт: число продлений × цена."""
    if not service.is_live or days_left > horizon_days:
        return 0
    period_days = service.term_hours / 24
    renewals = 1 + int((horizon_days - max(days_left, 0.0)) // period_days)
    return renewals * service.price


def revenue_over_horizon(service: Service, horizon_days: int) -> float:
    """Выручка услуги за горизонт при сохранении клиента (цена, приведённая к длине горизонта)."""
    return service.price * horizon_days * 24 / service.term_hours


LTV_CAP_MONTHS = 36


def ltv(service: Service, monthly_churn_pct: float) -> float:
    """Выручка услуги за ожидаемый срок жизни клиента: месячная выручка / базовый месячный отток (не больше 36 мес.)."""
    monthly = service.price * 730 / service.term_hours
    return monthly * min(100 / monthly_churn_pct, LTV_CAP_MONTHS)


def pay_capacity(account: Account, services: list[Service], as_of: datetime, horizon_days: int) -> tuple[float, float]:
    """Сколько клиент реально платит: средний платёж в месяц и сколько он может заплатить за горизонт.

    Средний платёж = всё пополненное / месяцы с первой услуги (не меньше одного). Почасовой сервер
    по прайсу может стоить тысячи евро в месяц, но клиент не заплатит больше, чем платил до сих пор
    или чем лежит у него на счёте.
    """
    start = min((s.createdAt for s in services), default=as_of)
    months = max(1.0, (as_of - start).total_seconds() / 86400 / 30)
    monthly = account.totalReplenished / months
    return monthly, max(float(account.available_funds), monthly * horizon_days / 30)


def evaluate(snapshot: Snapshot, settings: ModelSettings, now: datetime | None = None) -> EngineResult:
    as_of = resolve_as_of(settings, snapshot, now)
    by_owner, tx_stats = _group(snapshot.services, snapshot.transactions)
    customers = [_evaluate_customer(a, by_owner.get(a.id, []), tx_stats, settings, as_of) for a in snapshot.accounts]
    summary = _summarize(customers, settings, as_of)
    return EngineResult(as_of, settings, customers, summary, {c.account.id: c for c in customers})


def reevaluate(previous: EngineResult, accounts: list[Account], services: list[Service],
               transactions: list[Transaction]) -> EngineResult:
    """Пересчёт только затронутых клиентов (после событий биллинга).

    Оценка клиента зависит лишь от его аккаунта, услуг и транзакций, поэтому результат совпадает
    с полным пересчётом, а событие стоит миллисекунды вместо секунд на 100 000 клиентов.
    Сводка пересобирается из готовых оценок остальных клиентов.
    """
    settings, as_of = previous.settings, previous.as_of
    by_owner, tx_stats = _group(services, transactions)
    fresh = {a.id: _evaluate_customer(a, by_owner.get(a.id, []), tx_stats, settings, as_of) for a in accounts}
    customers = [fresh.get(c.account.id, c) for c in previous.customers]
    summary = _summarize(customers, settings, as_of)
    return EngineResult(as_of, settings, customers, summary, {c.account.id: c for c in customers})


def _group(services: list[Service], transactions: list[Transaction]) -> tuple[dict[int, list[Service]], dict[int, list[int]]]:
    """Услуги по владельцу и по каждой услуге [всего транзакций, неоплаченных]."""
    by_owner: dict[int, list[Service]] = defaultdict(list)
    for s in services:
        by_owner[s.ownerId].append(s)
    tx_stats: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for t in transactions:
        tx_stats[t.serviceId][0] += 1
        tx_stats[t.serviceId][1] += t.status != "performed"
    return by_owner, tx_stats


def _days_left(service: Service, as_of: datetime) -> float:
    return (service.expiresAt - as_of).total_seconds() / 86400


def _evaluate_customer(
    account: Account,
    services: list[Service],
    tx_stats: dict[int, list[int]],
    settings: ModelSettings,
    as_of: datetime,
) -> CustomerResult:
    horizon = settings.horizonDays
    live = [s for s in services if s.is_live]
    days = {s.id: _days_left(s, as_of) for s in services}
    costs = {s.id: renewal_cost(s, days[s.id], horizon) for s in live}
    account_need = sum(costs.values())

    results: list[ServiceResult] = []
    for s in services:
        issues = _quality_issues(s, days[s.id])
        if not s.is_live:
            results.append(ServiceResult(s, None, 0.0, 0.0, 0.0, days[s.id], [], issues))
            continue
        total, unpaid = tx_stats.get(s.id, (0, 0))
        gone = sum(1 for x in services if x.status in ("deleted", "blocked"))
        ctx = ServiceContext(s, account, as_of, horizon, days[s.id], costs[s.id], account_need, total, unpaid,
                             len(services), gone)
        contributions = []
        for feature in FEATURES:
            weight = settings.weight(feature.key)
            if weight <= 0:
                continue
            signal = feature.evaluate(ctx)
            if signal is None or signal.strength <= 0:
                continue
            contributions.append(Contribution(
                feature.key, feature.title, weight, round(signal.strength, 4),
                round(weight * signal.strength, 2), signal.reason, s.id, s.name,
            ))
        score = min(100.0, sum(c.points for c in contributions))
        probability = score / 100
        revenue = revenue_over_horizon(s, horizon)
        contributions.sort(key=lambda c: c.points, reverse=True)
        results.append(ServiceResult(s, score, probability, revenue, revenue * probability, days[s.id], contributions, issues,
                                     ltv(s, settings.ltvMonthlyChurn)))

    quality = [f"{r.service.name}: {i}" for r in results for i in r.quality_issues]
    if not live:
        level, note = _no_live_level(services)
        return CustomerResult(account, None, level, note, None, 0.0, 0.0, 0, 0.0, results, [], [], quality)

    live_results = [r for r in results if r.score is not None]
    score = round(max(r.score for r in live_results))
    level = "high" if score >= settings.thresholds.high else "medium" if score >= settings.thresholds.medium else "low"
    contributions = sorted((c for r in live_results for c in r.contributions), key=lambda c: c.points, reverse=True)
    top = max(live_results, key=lambda r: r.score)
    top_reason = top.contributions[0].reason if top.contributions else "Признаков риска нет"
    expected_loss = sum(r.expected_loss for r in live_results)
    revenue = sum(r.revenue_horizon for r in live_results)
    monthly, capacity = pay_capacity(account, services, as_of, horizon)
    return CustomerResult(
        account=account,
        score=score,
        level=level,
        note=None,
        top_reason=top_reason,
        expected_loss=expected_loss,
        revenue_horizon=revenue,
        renewal_need=account_need,
        ltv_at_risk=sum(r.ltv * r.probability for r in live_results),
        services=results,
        contributions=contributions,
        actions=_actions(account, live_results, account_need, as_of),
        quality_issues=quality,
        paid_monthly=monthly,
        expected_loss_paid=expected_loss * min(1.0, capacity / revenue) if revenue else 0.0,
    )


def _no_live_level(services: list[Service]) -> tuple[str, str]:
    if not services:
        return "no_score", "Нет оценки: у клиента нет услуг"
    blocked = sum(1 for s in services if s.status == "blocked")
    if blocked:
        return "no_score", f"Нет оценки: нет действующих услуг — заблокировано {blocked} из {len(services)}"
    last = max(s.expiresAt for s in services) + GRACE_PERIOD
    return "churned", (
        f"Все услуги удалены ({len(services)}). Последнее удаление ≈ {last:%d.%m.%Y} "
        "(дата восстановлена по правилу: срок + 5 дней)"
    )


def _quality_issues(service: Service, days_left: float) -> list[str]:
    grace_days = GRACE_PERIOD.total_seconds() / 86400
    if service.status == "prolong_wait" and days_left + grace_days < 0:
        return [f"ждёт оплаты дольше обычных 5 дней — удаление задержано на {humanize_days(-(days_left + grace_days))}; проверить в биллинге"]
    if service.status in ("active", "suspended") and days_left < -1:
        return [f"срок истёк {humanize_days(-days_left)} назад, а статус «{STATUS_TITLES[service.status]}»"]
    return []


def _actions(account: Account, live: list[ServiceResult], need: int, as_of: datetime) -> list[str]:
    actions: list[str] = []
    keys = {c.feature for r in live for c in r.contributions}
    if "insufficient_funds" in keys:
        deficit = need - account.available_funds
        due = max(as_of, min(r.service.expiresAt for r in live))
        when = "сегодня" if due.date() == as_of.date() else f"до {due:%d.%m}"
        actions.append(f"Предложить пополнить счёт на {fmt_cents(deficit)} {when}")
    for r in sorted(live, key=lambda r: r.days_left):
        feats = {c.feature for c in r.contributions}
        name = r.service.name
        if "prolong_wait" in feats:
            deadline = r.service.expiresAt + GRACE_PERIOD
            if deadline <= as_of:
                actions.append(f"Срочно связаться: {name} давно ждёт оплаты, удаление задержано — клиента ещё можно вернуть")
            else:
                actions.append(f"Срочно связаться: {name} удалится {deadline:%d.%m %H:%M}, если не оплатить")
        elif "expiring_no_autoprolong" in feats:
            if r.days_left <= 0:
                actions.append(f"Предложить продлить {name} сейчас: срок истёк, автопродление выключено")
            else:
                actions.append(f"Напомнить о продлении {name} до {r.service.expiresAt:%d.%m} и предложить включить автопродление")
        elif "suspended" in feats:
            actions.append(f"Узнать, нужен ли ещё сервер {name}: предложить тариф дешевле или помощь с настройкой")
    if not actions and "unpaid_transactions" in keys:
        actions.append("Напомнить об оплате неоплаченных счетов")
    return list(dict.fromkeys(actions))[:3]


def _summarize(customers: list[CustomerResult], settings: ModelSettings, as_of: datetime) -> Summary:
    levels = {lvl: 0 for lvl in LEVELS}
    level_loss = {lvl: 0.0 for lvl in LEVELS}
    for c in customers:
        levels[c.level] += 1
        level_loss[c.level] += c.expected_loss
    at_risk = [c for c in customers if c.level in ("high", "medium")]
    return Summary(
        as_of=as_of,
        horizon_days=settings.horizonDays,
        customers_total=len(customers),
        levels=levels,
        level_loss=level_loss,
        expected_loss=sum(c.expected_loss for c in customers),
        ltv_at_risk=sum(c.ltv_at_risk for c in customers),
        worst_case=sum(c.revenue_horizon for c in customers if c.level == "high"),
        revenue_at_risk=sum(c.revenue_horizon for c in at_risk),
        balance_at_risk=sum(c.account.available_funds for c in at_risk),
        services_at_risk=sum(
            1 for c in at_risk for r in c.services
            if r.score is not None and r.score >= settings.thresholds.medium
        ),
        quality_issues=sum(len(c.quality_issues) for c in customers),
        causes=_causes(at_risk),
        expected_loss_paid=sum(c.expected_loss_paid for c in customers),
    )


def _causes(at_risk: list[CustomerResult]) -> list[dict]:
    """Клиенты высокого и среднего риска по главной причине: кому какую кампанию запускать."""
    titles = {f.key: f.title for f in FEATURES}
    groups: dict[str, dict] = {}
    for c in at_risk:
        key = c.main_cause
        if key is None:
            continue
        g = groups.setdefault(key, {"feature": key, "title": titles.get(key, key),
                                    "kind": "involuntary" if key in INVOLUNTARY else "voluntary",
                                    "customers": 0, "expected_loss": 0.0, "expected_loss_paid": 0.0})
        g["customers"] += 1
        g["expected_loss"] += c.expected_loss
        g["expected_loss_paid"] += c.expected_loss_paid
    return sorted(groups.values(), key=lambda g: -g["expected_loss"])


def timeline(result: EngineResult, snapshot: Snapshot, transitions: list[Transition]) -> list[TimelinePoint]:
    """Отток по дням за горизонт назад и вперёд: факт (из журнала и восстановленный по правилу) и прогноз."""
    horizon, as_of = result.settings.horizonDays, result.as_of
    start = (as_of - timedelta(days=horizon)).date()
    days = [start + timedelta(days=i) for i in range(2 * horizon + 1)]
    points = {d: TimelinePoint(d.isoformat()) for d in days}
    recorded = _fact_from_journal(points, snapshot, transitions, as_of, horizon)
    _fact_by_rule(points, snapshot, recorded, as_of, horizon)
    _forecast(points, result, as_of, horizon)
    for p in points.values():
        p.forecast = round(p.forecast, 3)
        p.forecast_eur = round(p.forecast_eur, 2)
        p.fact_recorded_eur = round(p.fact_recorded_eur, 2)
        p.fact_restored_eur = round(p.fact_restored_eur, 2)
    return [points[d] for d in days]


def _fact_from_journal(points: dict, snapshot: Snapshot, transitions: list[Transition], as_of: datetime, horizon: int) -> set[int]:
    """Удаления, которые Радар видел сам (журнал смен статусов). Возвращает id этих услуг."""
    services = {s.id: s for s in snapshot.services}
    recorded: set[int] = set()
    for t in transitions:
        if t.to_status != "deleted" or t.at > as_of:
            continue
        recorded.add(t.service_id)
        p, s = points.get(t.at.date()), services.get(t.service_id)
        if p:
            p.fact_recorded += 1
            p.fact_recorded_eur += revenue_over_horizon(s, horizon) / 100 if s else 0
    return recorded


def _fact_by_rule(points: dict, snapshot: Snapshot, recorded: set[int], as_of: datetime, horizon: int) -> None:
    """Удаления из снимка без записи в журнале: дата восстановлена по правилу «срок + 5 дней»."""
    for s in snapshot.services:
        if s.status != "deleted" or s.id in recorded:
            continue
        p = points.get(min(s.expiresAt + GRACE_PERIOD, as_of).date())
        if p:
            p.fact_restored += 1
            p.fact_restored_eur += revenue_over_horizon(s, horizon) / 100


def _forecast(points: dict, result: EngineResult, as_of: datetime, horizon: int) -> None:
    """Ожидаемые непродления: в день, когда услуга перестанет оплачиваться (для «ждёт оплаты» — день удаления)."""
    end = as_of + timedelta(days=horizon)
    for c in result.customers:
        for r in c.services:
            if r.score is None or r.probability == 0:
                continue
            s = r.service
            event = s.expiresAt + GRACE_PERIOD if s.status == "prolong_wait" else s.expiresAt
            p = points.get(max(event, as_of).date()) if event <= end else None
            if p:
                p.forecast += r.probability
                p.forecast_eur += r.expected_loss / 100


__all__ = [
    "LEVELS", "LEVEL_TITLES", "STATUS_TITLES", "EngineResult", "CustomerResult", "ServiceResult",
    "Summary", "Transition", "TimelinePoint", "evaluate", "reevaluate", "timeline", "days_word",
]
