"""Снимок данных источника в памяти: аккаунты, услуги, транзакции.

Имена полей совпадают с источником (data.json и схема 001_schema.up.sql).
Все суммы — целые евроценты, как в источнике.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

TERM_HOURS = {
    "hour": 1,
    "week": 168,
    "month": 730,
    "quarter_year": 2190,
    "half_year": 4380,
    "year": 8760,
}
SERVICE_STATUSES = ("active", "suspended", "prolong_wait", "deleted", "blocked")
LIVE_STATUSES = frozenset({"active", "suspended", "prolong_wait"})
# По описанию кейса prolong_wait держится 5 дней, затем услуга удаляется.
GRACE_PERIOD = timedelta(days=5)


@dataclass
class Account:
    id: int
    email: str
    balance: int
    withdrawBalance: int
    totalReplenished: int
    bonusBalance: int
    bonusState: str
    signals: dict = field(default_factory=dict)  # доп. сигналы из других систем, например supportTickets30d

    @property
    def available_funds(self) -> int:
        """Деньги, которыми можно оплатить продление: основной баланс и бонусы, если они разблокированы."""
        return self.balance + (self.bonusBalance if self.bonusState == "unlocked" else 0)


@dataclass
class Service:
    id: int
    name: str
    ownerId: int
    paymentTerm: str
    autoProlong: bool
    createdAt: datetime
    expiresAt: datetime
    status: str
    price: int
    signals: dict = field(default_factory=dict)  # например loadDrop, downgraded

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUSES

    @property
    def term_hours(self) -> int:
        return TERM_HOURS[self.paymentTerm]


@dataclass
class Transaction:
    id: int
    amount: int
    status: str
    performedAt: datetime | None
    createdAt: datetime
    type: str
    serviceId: int


@dataclass
class Snapshot:
    accounts: list[Account] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    reference_at: datetime | None = None

    def latest_timestamp(self) -> datetime | None:
        """Самый поздний момент, известный из данных (без будущих expiresAt)."""
        moments = [s.createdAt for s in self.services]
        moments += [t.createdAt for t in self.transactions]
        moments += [t.performedAt for t in self.transactions if t.performedAt]
        return max(moments) if moments else None
