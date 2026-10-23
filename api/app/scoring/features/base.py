from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain import Account, Service


@dataclass(frozen=True)
class Signal:
    """Что признак увидел у услуги: сила от 0 до 1 и объяснение для маркетолога."""

    strength: float
    reason: str


@dataclass
class ServiceContext:
    """Всё, что признак может знать об услуге. Поля считает движок один раз."""

    service: Service
    account: Account
    as_of: datetime
    horizon_days: int
    days_left: float              # дней до expiresAt; отрицательное — срок прошёл
    renewal_cost: int             # сколько стоит держать услугу в горизонте, евроценты
    account_renewal_cost: int     # то же по всем живым услугам аккаунта
    tx_total: int
    tx_unpaid: int
    account_services_total: int = 0   # все услуги аккаунта
    account_services_gone: int = 0    # из них удалённые и заблокированные


class Feature:
    """Признак риска.

    Чтобы добавить признак, достаточно создать файл в этой папке с наследником Feature:
    реестр находит его сам (см. __init__.py). Вес появится в настройках, API, Swagger,
    на экране «Модель» и в метриках автоматически.
    """

    key: str
    title: str
    description: str
    default_weight: float
    order: int = 100  # порядок показа в панели

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        raise NotImplementedError


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def eur(cents: int) -> str:
    """12345 -> «€ 123,45» с неразрывным пробелом между разрядами."""
    whole, frac = divmod(abs(cents), 100)
    sign = "−" if cents < 0 else ""
    return f"{sign}€ {whole:,}".replace(",", " ") + f",{frac:02d}"


def days_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} дня"
    return f"{n} дней"


def humanize_days(days: float) -> str:
    if abs(days) < 1:
        hours = round(abs(days) * 24)
        return "меньше часа" if hours == 0 else f"{hours} ч"
    return days_word(round(days))
