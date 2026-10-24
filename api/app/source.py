"""DTO снимка источника и его проверка.

Формат — как data.json организаторов: {accounts, services, transactions}.
Проверки повторяют ограничения 001_schema.up.sql, чтобы ошибка была понятной (422),
а не падала на уровне базы.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain import Account, Service, Snapshot, Transaction

NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("дата должна быть с часовым поясом (например, ...Z)")
    return v


class AccountIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = Field(ge=1)
    email: str = Field(min_length=3)
    balance: int = Field(ge=0, description="Евроценты")
    withdrawBalance: int = Field(0, ge=0)
    totalReplenished: int = Field(0, ge=0)
    bonusBalance: int = Field(0, ge=0)
    bonusState: Literal["locked", "unlocked"] = "locked"
    signals: dict[str, float | bool] = Field(
        default_factory=dict,
        description="Необязательно: сигналы из других систем, например supportTickets30d — обращений в поддержку за 30 дней",
    )


class ServiceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = Field(ge=1)
    name: str
    ownerId: int
    paymentTerm: Literal["hour", "week", "month", "quarter_year", "half_year", "year"]
    autoProlong: bool = False
    createdAt: datetime
    expiresAt: datetime
    status: Literal["active", "blocked", "deleted", "suspended", "prolong_wait"]
    price: int = Field(ge=0, description="Евроценты за период paymentTerm")
    signals: dict[str, float | bool] = Field(
        default_factory=dict,
        description="Необязательно: loadDrop — падение нагрузки сервера 0–1; downgraded — перешёл на тариф дешевле",
    )

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("допустимы только латинские буквы, цифры и дефис")
        return v

    _tz = field_validator("createdAt", "expiresAt")(_aware)

    @model_validator(mode="after")
    def _dates(self) -> "ServiceIn":
        if self.expiresAt < self.createdAt:
            raise ValueError("expiresAt раньше createdAt")
        return self


class TransactionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = Field(ge=1)
    amount: int = Field(ge=0, le=1000)
    status: Literal["performed", "not_performed"] = "not_performed"
    performedAt: datetime | None = None
    createdAt: datetime
    type: Literal["prolong", "order"]
    serviceId: int

    _tz = field_validator("createdAt", "performedAt")(_aware)

    @model_validator(mode="after")
    def _performed(self) -> "TransactionIn":
        if self.status == "not_performed" and self.performedAt is not None:
            raise ValueError("у not_performed не может быть performedAt")
        if self.status == "performed":
            if self.performedAt is None:
                raise ValueError("у performed обязательна performedAt")
            if self.performedAt < self.createdAt:
                raise ValueError("performedAt раньше createdAt")
        return self


class SnapshotIn(BaseModel):
    """Полный снимок данных источника."""

    model_config = ConfigDict(extra="ignore", json_schema_extra={"example": {
        "referenceAt": "2026-10-19T15:00:00.000Z",
        "accounts": [{"id": 1, "email": "user-0001@example.test", "balance": 83810, "withdrawBalance": 14592,
                      "totalReplenished": 96922, "bonusBalance": 4506, "bonusState": "locked"}],
        "services": [{"id": 1, "name": "service-0001", "ownerId": 1, "paymentTerm": "hour", "autoProlong": False,
                      "createdAt": "2026-10-19T14:30:00.000Z", "expiresAt": "2026-10-19T15:30:00.000Z",
                      "status": "active", "price": 95}],
        "transactions": [{"id": 1, "amount": 0, "status": "performed", "performedAt": "2026-10-19T14:32:45.000Z",
                          "createdAt": "2026-10-19T14:32:00.000Z", "type": "order", "serviceId": 1}],
    }})
    referenceAt: datetime | None = Field(None, description="Момент, на который сделан снимок (необязательно)")
    accounts: list[AccountIn]
    services: list[ServiceIn]
    transactions: list[TransactionIn]

    _tz = field_validator("referenceAt")(_aware)

    @model_validator(mode="after")
    def _integrity(self) -> "SnapshotIn":
        _unique(self.accounts, "accounts")
        _unique(self.services, "services")
        _unique(self.transactions, "transactions")
        emails = [a.email for a in self.accounts]
        if len(emails) != len(set(emails)):
            raise ValueError("accounts: email должен быть уникальным")
        account_ids = {a.id for a in self.accounts}
        for i, s in enumerate(self.services):
            if s.ownerId not in account_ids:
                raise ValueError(f"services[{i}].ownerId: аккаунт {s.ownerId} не найден")
        service_ids = {s.id for s in self.services}
        for i, t in enumerate(self.transactions):
            if t.serviceId not in service_ids:
                raise ValueError(f"transactions[{i}].serviceId: услуга {t.serviceId} не найдена")
        return self

    def to_snapshot(self) -> Snapshot:
        return Snapshot(
            accounts=[Account(**a.model_dump()) for a in self.accounts],
            services=[Service(**s.model_dump()) for s in self.services],
            transactions=[Transaction(**t.model_dump()) for t in self.transactions],
            reference_at=self.referenceAt,
        )


def _unique(items: list, name: str) -> None:
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name}: id должны быть уникальными")
