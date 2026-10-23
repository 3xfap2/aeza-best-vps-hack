"""Расчёт риска оттока.

Score услуги = min(100, Σ вес признака × сила признака).
Score клиента = максимум по его живым услугам (active, suspended, prolong_wait).
Вероятность непродления услуги = score / 100.
Ожидаемая потеря = Σ выручка услуги за горизонт × вероятность непродления.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.domain import GRACE_PERIOD, Account, Service, Snapshot

from .features import FEATURES, ServiceContext
from .features.base import days_word, eur, humanize_days
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
    services: list[ServiceResult]
    contributions: list[Contribution]
    actions: list[str]
    quality_issues: list[str]


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
    expected_loss: float
    worst_case: float
    revenue_at_risk: float
    balance_at_risk: int
    services_at_risk: int
    quality_issues: int


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


def evaluate(snapshot: Snapshot, settings: ModelSettings, now: datetime | None = None) -> EngineResult:
    as_of = resolve_as_of(settings, snapshot, now)
    horizon = settings.horizonDays

    services_by_owner: dict[int, list[Service]] = defaultdict(list)
    for s in snapshot.services:
        services_by_owner[s.ownerId].append(s)
    tx_stats: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for t in snapshot.transactions:
        stats = tx_stats[t.serviceId]
        stats[0] += 1
        if t.status != "performed":
            stats[1] += 1

    customers = [
        _evaluate_customer(a, services_by_owner.get(a.id, []), tx_stats, settings, as_of)
        for a in snapshot.accounts
    ]
    summary = _summarize(customers, settings, as_of)
    return EngineResult(as_of, settings, customers, summary, {c.account.id: c for c in customers})


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
        ctx = ServiceContext(s, account, as_of, horizon, days[s.id], costs[s.id], account_need, total, unpaid)
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
        results.append(ServiceResult(s, score, probability, revenue, revenue * probability, days[s.id], contributions, issues))

    quality = [f"{r.service.name}: {i}" for r in results for i in r.quality_issues]
    if not live:
        level, note = _no_live_level(services)
        return CustomerResult(account, None, level, note, None, 0.0, 0.0, 0, results, [], [], quality)

    live_results = [r for r in results if r.score is not None]
    score = round(max(r.score for r in live_results))
    level = "high" if score >= settings.thresholds.high else "medium" if score >= settings.thresholds.medium else "low"
    contributions = sorted((c for r in live_results for c in r.contributions), key=lambda c: c.points, reverse=True)
    top = max(live_results, key=lambda r: r.score)
    top_reason = top.contributions[0].reason if top.contributions else "Признаков риска нет"
    return CustomerResult(
        account=account,
        score=score,
        level=level,
        note=None,
        top_reason=top_reason,
        expected_loss=sum(r.expected_loss for r in live_results),
        revenue_horizon=sum(r.revenue_horizon for r in live_results),
        renewal_need=account_need,
        services=results,
        contributions=contributions,
        actions=_actions(account, live_results, account_need, as_of),
        quality_issues=quality,
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
        return [f"ждёт оплаты дольше 5 дней (удаление просрочено на {humanize_days(-(days_left + grace_days))}) — статус в источнике не обновлён"]
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
        actions.append(f"Предложить пополнить счёт на {eur(deficit)} {when}")
    for r in sorted(live, key=lambda r: r.days_left):
        feats = {c.feature for c in r.contributions}
        name = r.service.name
        if "prolong_wait" in feats:
            deadline = r.service.expiresAt + GRACE_PERIOD
            if deadline <= as_of:
                actions.append(f"Проверить {name} в биллинге: удаление просрочено, клиент ещё может вернуться")
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
    for c in customers:
        levels[c.level] += 1
    at_risk = [c for c in customers if c.level in ("high", "medium")]
    return Summary(
        as_of=as_of,
        horizon_days=settings.horizonDays,
        customers_total=len(customers),
        levels=levels,
        expected_loss=sum(c.expected_loss for c in customers),
        worst_case=sum(c.revenue_horizon for c in customers if c.level == "high"),
        revenue_at_risk=sum(c.revenue_horizon for c in at_risk),
        balance_at_risk=sum(c.account.available_funds for c in at_risk),
        services_at_risk=sum(
            1 for c in at_risk for r in c.services
            if r.score is not None and r.score >= settings.thresholds.medium
        ),
        quality_issues=sum(len(c.quality_issues) for c in customers),
    )


def timeline(result: EngineResult, snapshot: Snapshot, transitions: list[Transition]) -> list[TimelinePoint]:
    """Отток по дням: факт (зафиксированный журналом и восстановленный по правилу) и прогноз."""
    horizon = result.settings.horizonDays
    as_of = result.as_of
    start = (as_of - timedelta(days=horizon)).date()
    points = {start + timedelta(days=i): TimelinePoint((start + timedelta(days=i)).isoformat()) for i in range(2 * horizon + 1)}
    services = {s.id: s for s in snapshot.services}

    recorded = set()
    for t in transitions:
        if t.to_status != "deleted" or t.at > as_of:
            continue
        recorded.add(t.service_id)
        p = points.get(t.at.date())
        if p:
            p.fact_recorded += 1
            s = services.get(t.service_id)
            p.fact_recorded_eur += revenue_over_horizon(s, horizon) / 100 if s else 0

    for s in snapshot.services:
        if s.status != "deleted" or s.id in recorded:
            continue
        restored = s.expiresAt + GRACE_PERIOD
        if restored > as_of:
            restored = as_of
        p = points.get(restored.date())
        if p:
            p.fact_restored += 1
            p.fact_restored_eur += revenue_over_horizon(s, horizon) / 100

    end = as_of + timedelta(days=horizon)
    for c in result.customers:
        for r in c.services:
            if r.score is None or r.probability == 0:
                continue
            s = r.service
            event = s.expiresAt + GRACE_PERIOD if s.status == "prolong_wait" else s.expiresAt
            if event > end:
                continue
            event = max(event, as_of)
            p = points.get(event.date())
            if p:
                p.forecast += r.probability
                p.forecast_eur += r.expected_loss / 100

    for p in points.values():
        p.forecast = round(p.forecast, 3)
        p.forecast_eur = round(p.forecast_eur, 2)
        p.fact_recorded_eur = round(p.fact_recorded_eur, 2)
        p.fact_restored_eur = round(p.fact_restored_eur, 2)
    return [points[d] for d in sorted(points)]


__all__ = [
    "LEVELS", "LEVEL_TITLES", "STATUS_TITLES", "EngineResult", "CustomerResult", "ServiceResult",
    "Summary", "Transition", "TimelinePoint", "evaluate", "timeline", "days_word",
]
