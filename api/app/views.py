"""Модели ответов API и преобразование результата расчёта в них.

Все денежные поля ответов — в евро (источник хранит евроценты, €1 = 100).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain import Snapshot
from app.scoring.engine import (
    LEVEL_TITLES, STATUS_TITLES, Contribution, CustomerResult, EngineResult, ServiceResult, TimelinePoint,
)

Level = Literal["high", "medium", "low", "no_score", "churned"]


def eur(cents: float) -> float:
    return round(cents / 100, 2)


class UserOut(BaseModel):
    id: int
    login: str
    displayName: str
    viaSso: bool


class ContributionOut(BaseModel):
    feature: str
    title: str
    weight: float = Field(description="Вес признака из настроек")
    strength: float = Field(description="Сила сигнала 0–1")
    points: float = Field(description="Баллы = вес × сила")
    reason: str
    serviceId: int
    serviceName: str


class CustomerListItem(BaseModel):
    id: int
    email: str
    balance: float = Field(description="Основной баланс, €")
    totalReplenished: float = Field(description="Пополнено за всё время, €")
    score: int | None = Field(description="0–100; null — оценки нет (см. level и topReason)")
    loyalty: int | None = Field(description="Обратная шкала: 100 − score; null — оценки нет")
    level: Level
    levelTitle: str
    topReason: str | None
    expectedLoss: float = Field(description="Ожидаемая потеря за горизонт, €")
    qualityIssues: int = Field(description="Сколько противоречий найдено в данных клиента")


class CustomerPage(BaseModel):
    items: list[CustomerListItem]
    total: int
    page: int
    pageSize: int
    sort: str
    order: str
    asOf: datetime
    version: int


class ServiceOut(BaseModel):
    id: int
    name: str
    paymentTerm: str
    status: str
    statusTitle: str
    autoProlong: bool
    price: float = Field(description="Цена за период, €")
    createdAt: datetime
    expiresAt: datetime
    daysLeft: float
    score: float | None
    probability: float
    revenueHorizon: float = Field(description="Выручка услуги за горизонт, €")
    expectedLoss: float
    contributions: list[ContributionOut]
    qualityIssues: list[str]


class TransactionOut(BaseModel):
    id: int
    serviceId: int
    type: str
    amount: float
    status: str
    createdAt: datetime
    performedAt: datetime | None


class Finances(BaseModel):
    balance: float
    bonusBalance: float
    bonusState: str
    withdrawBalance: float
    totalReplenished: float
    availableFunds: float = Field(description="Баланс + разблокированные бонусы, €")
    renewalNeed: float = Field(description="Сколько нужно на продления всех услуг в горизонте, €")


class CustomerDetail(CustomerListItem):
    note: str | None
    revenueHorizon: float
    finances: Finances
    contributions: list[ContributionOut]
    actions: list[str]
    issues: list[str]
    services: list[ServiceOut]
    transactions: list[TransactionOut]
    asOf: datetime
    horizonDays: int
    version: int


class SummaryOut(BaseModel):
    asOf: datetime
    horizonDays: int
    customersTotal: int
    levels: dict[str, int]
    levelLoss: dict[str, float] = Field(description="Ожидаемая потеря по уровням риска, €")
    expectedLoss: float = Field(description="Σ выручка услуг за горизонт × вероятность непродления, €")
    worstCase: float = Field(description="Вся выручка за горизонт клиентов высокого риска, €")
    revenueAtRisk: float = Field(description="Выручка за горизонт клиентов высокого и среднего риска, €")
    balanceAtRisk: float = Field(description="Доступные деньги на счетах клиентов высокого и среднего риска, €")
    servicesAtRisk: int
    qualityIssues: int
    causes: list[CauseOut] = Field(description="Клиенты высокого и среднего риска по главной причине")
    explanation: str
    version: int
    computedAt: datetime
    durationMs: float


class CauseOut(BaseModel):
    feature: str
    title: str
    kind: Literal["involuntary", "voluntary"] = Field(description="involuntary — не прошла оплата; voluntary — решение клиента")
    customers: int
    expectedLoss: float


class TimelinePointOut(BaseModel):
    date: str
    factRecorded: int = Field(description="Удаления, зафиксированные журналом")
    factRestored: int = Field(description="Удаления с датой, восстановленной по правилу expiresAt + 5 дней")
    forecast: float = Field(description="Ожидаемое число непродлений (сумма вероятностей)")
    factRecordedEur: float
    factRestoredEur: float
    forecastEur: float


class TimelineOut(BaseModel):
    asOf: datetime
    horizonDays: int
    points: list[TimelinePointOut]
    summary: str


def contribution_out(c: Contribution) -> ContributionOut:
    return ContributionOut(
        feature=c.feature, title=c.title, weight=c.weight, strength=c.strength, points=c.points,
        reason=c.reason, serviceId=c.service_id, serviceName=c.service_name,
    )


def list_item(c: CustomerResult) -> CustomerListItem:
    return CustomerListItem(
        id=c.account.id,
        email=c.account.email,
        balance=eur(c.account.balance),
        totalReplenished=eur(c.account.totalReplenished),
        score=c.score,
        loyalty=None if c.score is None else 100 - c.score,
        level=c.level,
        levelTitle=LEVEL_TITLES[c.level],
        topReason=c.top_reason or c.note,
        expectedLoss=eur(c.expected_loss),
        qualityIssues=len(c.quality_issues),
    )


def service_out(r: ServiceResult) -> ServiceOut:
    s = r.service
    return ServiceOut(
        id=s.id, name=s.name, paymentTerm=s.paymentTerm, status=s.status, statusTitle=STATUS_TITLES[s.status],
        autoProlong=s.autoProlong, price=eur(s.price), createdAt=s.createdAt, expiresAt=s.expiresAt,
        daysLeft=round(r.days_left, 2), score=round(r.score, 2) if r.score is not None else None,
        probability=round(r.probability, 4), revenueHorizon=eur(r.revenue_horizon),
        expectedLoss=eur(r.expected_loss), contributions=[contribution_out(c) for c in r.contributions],
        qualityIssues=r.quality_issues,
    )


def customer_detail(c: CustomerResult, result: EngineResult, snapshot: Snapshot, version: int) -> CustomerDetail:
    a = c.account
    service_ids = {r.service.id for r in c.services}
    txs = [t for t in snapshot.transactions if t.serviceId in service_ids]
    txs.sort(key=lambda t: t.createdAt, reverse=True)
    return CustomerDetail(
        **list_item(c).model_dump(),
        note=c.note,
        revenueHorizon=eur(c.revenue_horizon),
        finances=Finances(
            balance=eur(a.balance), bonusBalance=eur(a.bonusBalance), bonusState=a.bonusState,
            withdrawBalance=eur(a.withdrawBalance), totalReplenished=eur(a.totalReplenished),
            availableFunds=eur(a.available_funds), renewalNeed=eur(c.renewal_need),
        ),
        contributions=[contribution_out(x) for x in c.contributions],
        actions=c.actions,
        issues=c.quality_issues,
        services=[service_out(r) for r in sorted(c.services, key=lambda r: (r.score is None, -(r.score or 0)))],
        transactions=[
            TransactionOut(id=t.id, serviceId=t.serviceId, type=t.type, amount=eur(t.amount), status=t.status,
                           createdAt=t.createdAt, performedAt=t.performedAt)
            for t in txs
        ],
        asOf=result.as_of,
        horizonDays=result.settings.horizonDays,
        version=version,
    )


def summary_out(result: EngineResult, version: int, computed_at: datetime, duration_ms: float) -> SummaryOut:
    s = result.summary
    return SummaryOut(
        asOf=s.as_of,
        horizonDays=s.horizon_days,
        customersTotal=s.customers_total,
        levels=s.levels,
        levelLoss={k: eur(v) for k, v in s.level_loss.items()},
        expectedLoss=eur(s.expected_loss),
        worstCase=eur(s.worst_case),
        revenueAtRisk=eur(s.revenue_at_risk),
        balanceAtRisk=eur(s.balance_at_risk),
        servicesAtRisk=s.services_at_risk,
        qualityIssues=s.quality_issues,
        causes=[CauseOut(feature=g["feature"], title=g["title"], kind=g["kind"], customers=g["customers"],
                         expectedLoss=eur(g["expected_loss"])) for g in s.causes],
        explanation=(
            f"Ожидаемая потеря — сколько выручки за {s.horizon_days} дн. бизнес, скорее всего, не получит: "
            "для каждой действующей услуги цена, приведённая к горизонту, умножается на вероятность непродления "
            "(score услуги / 100). Баланс клиента уже учтён в риске через признак «Не хватает средств»."
        ),
        version=version,
        computedAt=computed_at,
        durationMs=duration_ms,
    )


def timeline_out(result: EngineResult, points: list[TimelinePoint]) -> TimelineOut:
    horizon = result.settings.horizonDays
    fact = sum(p.fact_recorded + p.fact_restored for p in points)
    forecast = sum(p.forecast for p in points)
    return TimelineOut(
        asOf=result.as_of,
        horizonDays=horizon,
        points=[
            TimelinePointOut(
                date=p.date, factRecorded=p.fact_recorded, factRestored=p.fact_restored,
                forecast=p.forecast, factRecordedEur=p.fact_recorded_eur,
                factRestoredEur=p.fact_restored_eur, forecastEur=p.forecast_eur,
            )
            for p in points
        ],
        summary=(
            f"За {horizon} дн. до даты расчёта удалено услуг: {fact}. "
            f"В ближайшие {horizon} дн. ожидается непродлений: {forecast:.1f}."
        ),
    )
