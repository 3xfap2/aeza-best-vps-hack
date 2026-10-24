#!/usr/bin/env python3
"""Расширенный набор данных «для наглядности» (разрешено кейсом): данные кейса + сгенерированные клиенты.

Формат и ограничения — как в data.json организаторов. В отличие от их генератора,
клиенты собраны из правдоподобных сценариев (лояльный, забывчивый, без денег,
ждёт оплаты, выключил сервер, ушёл...) и статусы согласованы с датами.
Только стандартная библиотека; результат воспроизводим (--seed).

    python tools/gen_demo.py --accounts 200 --out data/demo/data.json
"""
from __future__ import annotations

import argparse
import calendar
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

TERM_HOURS = {"hour": 1, "week": 168, "month": 730, "quarter_year": 2190, "half_year": 4380, "year": 8760}
MONTHS = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}
# Цена за период в евроцентах: (мин, макс) для месячного эквивалента и множитель периода.
MONTHLY_PRICE = (400, 4500)
TERM_FACTOR = {"hour": 1 / 730, "week": 0.25, "month": 1, "quarter_year": 2.85, "half_year": 5.4, "year": 10.2}
TERM_WEIGHTS = {"hour": 8, "week": 6, "month": 50, "quarter_year": 14, "half_year": 8, "year": 14}
NAMES = ["web", "db", "vpn", "game", "shop", "api", "mail", "backup", "ci", "cdn", "bot", "crm"]

# Доли сценариев подобраны под ориентиры рынка подписок/хостинга: отток 3–7 % в месяц,
# недобровольная часть (неоплата) 20–50 % оттока. Источники — в docs/MODEL.md.
PERSONAS = [  # (имя, доля)
    ("loyal", 63), ("forgetful", 10), ("short_on_money", 4), ("waiting", 3),
    ("sleeping", 7), ("churned", 9), ("blocked", 2), ("new_unpaid", 2),
]


def iso(v: datetime) -> str:
    return v.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_term(v: datetime, term: str, n: int = 1) -> datetime:
    if term not in MONTHS:
        return v + timedelta(hours=TERM_HOURS[term] * n)
    idx = v.year * 12 + v.month - 1 + MONTHS[term] * n
    y, m = divmod(idx, 12)
    m += 1
    return v.replace(year=y, month=m, day=min(v.day, calendar.monthrange(y, m)[1]))


class Gen:
    def __init__(self, seed: int, ref: datetime, base: dict | None = None) -> None:
        self.rng = random.Random(seed)
        self.ref = ref
        # Расширение тестового набора: записи кейса идут первыми и без изменений.
        self.accounts: list[dict] = list(base["accounts"]) if base else []
        self.services: list[dict] = list(base["services"]) if base else []
        self.transactions: list[dict] = list(base["transactions"]) if base else []

    def pick(self, weights: dict[str, int]) -> str:
        return self.rng.choices(list(weights), weights=list(weights.values()))[0]

    def price(self, term: str) -> int:
        monthly = self.rng.randint(*MONTHLY_PRICE)
        return max(1, round(monthly * TERM_FACTOR[term]))

    def account(self, balance: int, bonus_unlocked: bool | None = None) -> dict:
        i = len(self.accounts) + 1
        bonus = self.rng.choice([0, 0, 0, self.rng.randint(100, 3000)])
        acc = {
            "id": i, "email": f"user-{i:04d}@example.test", "balance": balance,
            "withdrawBalance": self.rng.choice([0, 0, 0, self.rng.randint(0, max(balance // 4, 1))]),
            "totalReplenished": balance + self.rng.randint(1000, 300_000),
            "bonusBalance": bonus,
            "bonusState": "unlocked" if (bonus_unlocked if bonus_unlocked is not None else self.rng.random() < 0.5) else "locked",
        }
        acc["withdrawBalance"] = min(acc["withdrawBalance"], balance)
        self.accounts.append(acc)
        return acc

    def service(self, acc: dict, term: str, status: str, autoprolong: bool, expires: datetime,
                price: int | None = None, unpaid_share: float = 0.0) -> dict:
        i = len(self.services) + 1
        periods = self.rng.randint(1, 8)
        created = expires
        for _ in range(periods):
            prev = created
            created = add_term(created, term, -1) if term in MONTHS else created - timedelta(hours=TERM_HOURS[term])
            if created >= prev:
                break
        created = min(created, self.ref - timedelta(minutes=5))
        svc = {
            "id": i, "name": f"{self.rng.choice(NAMES)}-{i:04d}", "ownerId": acc["id"], "paymentTerm": term,
            "autoProlong": autoprolong, "createdAt": iso(created), "expiresAt": iso(max(expires, created)),
            "status": status, "price": price if price is not None else self.price(term),
        }
        self.services.append(svc)
        self.history(svc, created, min(periods, 6), unpaid_share)
        return svc

    def history(self, svc: dict, created: datetime, renewals: int, unpaid_share: float) -> None:
        moments = [created] + [created + (self.ref - created) * (k + 1) / (renewals + 1) for k in range(renewals)]
        for n, at in enumerate(moments):
            paid = self.rng.random() >= unpaid_share
            t_id = len(self.transactions) + 1
            self.transactions.append({
                "id": t_id, "amount": min(svc["price"], 1000),
                "status": "performed" if paid else "not_performed",
                "performedAt": iso(at + timedelta(seconds=self.rng.randint(5, 600))) if paid else None,
                "createdAt": iso(at), "type": "order" if n == 0 else "prolong", "serviceId": svc["id"],
            })

    def fund(self, acc: dict, services: list[dict]) -> None:
        """Баланс, которого хватает на ближайшие продления всех услуг клиента."""
        need = sum(s["price"] for s in services)
        acc["balance"] = round(need * self.rng.uniform(1.3, 3.5))
        acc["totalReplenished"] = acc["balance"] + self.rng.randint(1000, 300_000)

    def days(self, low: float, high: float) -> datetime:
        return self.ref + timedelta(days=self.rng.uniform(low, high))

    def customer(self, persona: str) -> None:
        r = self.rng
        n = r.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]
        terms = [self.pick(TERM_WEIGHTS) for _ in range(n)]
        monthly_spend = sum(self.price(t) / TERM_FACTOR[t] for t in terms)

        if persona == "loyal":
            acc = self.account(round(monthly_spend * r.uniform(3, 12)))
            for t in terms:
                self.service(acc, t, r.choice(["active"] * 9 + ["suspended"]), True, self.days(10, 300))
        elif persona == "forgetful":  # деньги есть, но автопродление выключено
            acc = self.account(0)
            svcs = [self.service(acc, "month" if t == "hour" else t, "active", k > 0 and r.random() < 0.5,
                                 self.days(0.3, 25) if k == 0 else self.days(5, 200))
                    for k, t in enumerate(terms)]
            self.fund(acc, svcs)
        elif persona == "short_on_money":
            acc = self.account(round(monthly_spend * r.uniform(0.05, 0.6)), bonus_unlocked=False)
            for k, t in enumerate(terms):
                self.service(acc, t, "active", True, self.days(0.2, 20) if k == 0 else self.days(1, 60))
        elif persona == "waiting":
            acc = self.account(round(monthly_spend * r.uniform(0, 0.4)))
            self.service(acc, terms[0] if terms[0] != "hour" else "month", "prolong_wait",
                         r.random() < 0.5, self.days(-4.8, -0.1), unpaid_share=0.4)
            for t in terms[1:]:
                self.service(acc, t, "active", True, self.days(3, 90))
        elif persona == "sleeping":  # выключил сервер и не продлевает
            acc = self.account(0)
            svcs = [self.service(acc, "month" if t == "hour" else t, "suspended", r.random() < 0.3,
                                 self.days(2, 40), unpaid_share=0.3) for t in terms]
            self.fund(acc, svcs)
        elif persona == "churned":
            acc = self.account(r.randint(0, 2000))
            for t in terms:
                self.service(acc, t if t != "hour" else "month", "deleted", r.random() < 0.3,
                             self.days(-60, -5.2), unpaid_share=0.5)
        elif persona == "blocked":
            acc = self.account(r.randint(0, 5000))
            for t in terms:
                self.service(acc, t, "blocked", False, self.days(-30, 60))
        else:  # new_unpaid: свежий заказ не оплачен
            acc = self.account(r.randint(0, 1500))
            self.service(acc, terms[0] if terms[0] != "hour" else "month", "active", False,
                         self.days(1, 25), unpaid_share=1.0)

    def add_signals(self, first_generated: int) -> None:
        """Сигналы из других систем у части сгенерированных клиентов (у клиентов кейса их нет)."""
        r = self.rng
        for acc in self.accounts[first_generated:]:
            if r.random() < 0.08:
                acc["signals"] = {"supportTickets30d": r.randint(1, 5)}
        for svc in self.services:
            if svc["ownerId"] <= first_generated or svc["status"] in ("deleted", "blocked"):
                continue
            sig = {}
            if (svc["status"] == "suspended" and r.random() < 0.4) or r.random() < 0.03:
                sig["loadDrop"] = round(r.uniform(0.5, 0.95), 2)
            if r.random() < 0.03:
                sig["downgraded"] = True
            if sig:
                svc["signals"] = sig

    def build(self, count: int) -> dict:
        first_generated = len(self.accounts)
        count -= len(self.accounts)
        personas = [p for p, _ in PERSONAS]
        weights = [w for _, w in PERSONAS]
        for _ in range(count):
            self.customer(self.rng.choices(personas, weights=weights)[0])
        self.add_signals(first_generated)
        return {"referenceAt": iso(self.ref), "accounts": self.accounts,
                "services": self.services, "transactions": self.transactions}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--accounts", type=int, default=200)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--reference", default="2026-10-19T15:00:00.000Z")
    p.add_argument("--base", type=Path, default=Path("data/case/data.json"),
                   help="Тестовый набор кейса, который расширяем (пусто — без него)")
    p.add_argument("--out", type=Path, default=Path("data/demo/data.json"))
    a = p.parse_args()
    ref = datetime.fromisoformat(a.reference.replace("Z", "+00:00")).astimezone(timezone.utc)
    base = json.loads(a.base.read_text(encoding="utf-8")) if a.base and str(a.base) else None
    data = Gen(a.seed, ref, base).build(a.accounts)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{a.out}: {len(data['accounts'])} аккаунтов, {len(data['services'])} услуг, "
          f"{len(data['transactions'])} транзакций")


if __name__ == "__main__":
    main()
