#!/usr/bin/env python3
"""История к расширенному набору: прошлые снимки тех же клиентов за 30, 60 и 90 дней до даты снимка.

Нужна, чтобы модель могла сверить свои прошлые оценки с тем, что случилось потом (обратная связь):
кто из клиентов, оценённых месяц назад, не продлил услугу, а кто продлил.

Прошлое восстанавливается по текущему снимку правилами жизненного цикла кейса, поэтому оно с ним согласовано:
- удалённая услуга до удаления (срок + 5 дней) ждала оплаты, а до срока была активна;
- действующая услуга в прошлом была в том периоде оплаты, который покрывал ту дату, то есть потом её продлили;
- часть забывчивых клиентов оплатила с опозданием: в прошлом снимке услуга ждёт оплаты, сейчас активна;
- баланс в прошлом = текущий + оплаченные с тех пор продления − часть пополнений.
История есть только у сгенерированных клиентов: прошлого клиентов кейса мы не знаем.
Только стандартная библиотека; результат воспроизводим (--seed).

    python tools/gen_history.py --data data/demo/data.json --out data/demo/history.json
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gen_demo import MONTHS, TERM_HOURS, add_term, iso

GRACE = timedelta(days=5)
LIVE = ("active", "suspended", "prolong_wait")


def parse(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def period_at(expires: datetime, term: str, t: datetime) -> datetime:
    """Окончание периода оплаты, который шёл в момент t (цепочка продлений назад от expires)."""
    if term not in MONTHS:
        step = timedelta(hours=TERM_HOURS[term])
        k = int((expires - t) / step)
        e = expires - step * k
        return e if e > t else e + step
    k = 0
    while add_term(expires, term, -(k + 1)) > t:
        k += 1
    return add_term(expires, term, -k)


def renewals_between(expires_t: datetime, term: str, t_end: datetime) -> int:
    """Сколько продлений оплачено с окончания периода expires_t до t_end."""
    if term not in MONTHS:
        return max(0, int((t_end - expires_t) / timedelta(hours=TERM_HOURS[term])) + 1) if expires_t <= t_end else 0
    n, e = 0, expires_t
    while e <= t_end:
        n += 1
        e = add_term(e, term, 1)
    return n


def past_snapshot(data: dict, ref: datetime, t: datetime, first_generated: int, rng: random.Random) -> dict:
    services, paid = [], {}
    for s in data["services"]:
        if s["ownerId"] <= first_generated or parse(s["createdAt"]) > t:
            continue
        status, expires, term = s["status"], parse(s["expiresAt"]), s["paymentTerm"]
        past = dict(s)
        if status == "deleted":
            past["status"] = "deleted" if t >= expires + GRACE else "prolong_wait" if t >= expires else "active"
        elif status == "prolong_wait":
            past["status"] = "prolong_wait" if t >= expires else "active"
        elif status == "blocked":
            pass
        else:
            e = period_at(expires, term, t)
            past["expiresAt"] = iso(e)
            past["status"] = "suspended" if status == "suspended" and t >= ref - timedelta(days=35) else "active"
            paid[s["ownerId"]] = paid.get(s["ownerId"], 0) + renewals_between(e, term, ref) * s["price"]
            prev = add_term(e, term, -1) if term in MONTHS else None
            # оплатил с опозданием: месяц назад услуга ждала оплаты, потом её продлили
            if (prev and not s["autoProlong"] and t - GRACE < prev <= t and prev > parse(s["createdAt"])
                    and rng.random() < 0.6):
                past["status"], past["expiresAt"] = "prolong_wait", iso(prev)
        if t < ref - timedelta(days=35):
            past.pop("signals", None)
        services.append(past)

    owners = {s["ownerId"] for s in services}
    accounts = []
    for a in data["accounts"]:
        if a["id"] not in owners:
            continue
        past = dict(a)
        spent = paid.get(a["id"], 0)
        kept = round(spent * rng.uniform(0.3, 1.0))      # остальное клиент пополнил после этой даты
        past["balance"] = a["balance"] + kept
        past["withdrawBalance"] = min(a["withdrawBalance"], past["balance"])
        past["totalReplenished"] = max(past["balance"], a["totalReplenished"] - (spent - kept))
        if t < ref - timedelta(days=35):
            past.pop("signals", None)
        accounts.append(past)

    service_ids = {s["id"] for s in services}
    transactions = []
    for tx in data["transactions"]:
        if tx["serviceId"] not in service_ids or parse(tx["createdAt"]) > t:
            continue
        past = dict(tx)
        if past["performedAt"] and parse(past["performedAt"]) > t:
            past["status"], past["performedAt"] = "not_performed", None
        transactions.append(past)
    return {"referenceAt": iso(t), "accounts": accounts, "services": services, "transactions": transactions}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data/demo/data.json"))
    p.add_argument("--case", type=Path, default=Path("data/case/data.json"), help="Клиенты кейса — без истории")
    p.add_argument("--days", default="90,60,30", help="За сколько дней до снимка делать прошлые снимки")
    p.add_argument("--seed", type=int, default=2027)
    p.add_argument("--out", type=Path, default=Path("data/demo/history.json"))
    a = p.parse_args()
    data = json.loads(a.data.read_text(encoding="utf-8"))
    first_generated = max((x["id"] for x in json.loads(a.case.read_text(encoding="utf-8"))["accounts"]), default=0)
    ref = parse(data["referenceAt"])
    rng = random.Random(a.seed)
    snaps = [past_snapshot(data, ref, ref - timedelta(days=int(d)), first_generated, rng)
             for d in sorted((x for x in a.days.split(",")), key=lambda x: -int(x))]
    a.out.write_text(json.dumps({"snapshots": snaps}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for s in snaps:
        live = sum(1 for x in s["services"] if x["status"] in LIVE)
        print(f"{s['referenceAt']}: {len(s['accounts'])} аккаунтов, {len(s['services'])} услуг (действующих {live}), "
              f"{len(s['transactions'])} транзакций")


if __name__ == "__main__":
    main()
