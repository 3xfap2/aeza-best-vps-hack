#!/usr/bin/env python3
"""Значения для демо на лендинге — из расширенного набора и того же движка, что в панели.

Лендинг открыт без входа, поэтому живые данные он не запрашивает: всё, что на нём
показано (радар, рейтинг, «до → после», формула, график), посчитано этим скриптом
по data/demo/data.json и записано в web/src/design/landing-data.js.

    cd api && python ../tools/build_landing_data.py
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from app.scoring.engine import evaluate, revenue_over_horizon, timeline  # noqa: E402
from app.scoring.features import FEATURES  # noqa: E402
from app.scoring.settings import default_settings  # noqa: E402
from app.source import SnapshotIn  # noqa: E402

TERMS = {"hour": "почасово", "week": "неделя", "month": "месяц", "quarter_year": "квартал",
         "half_year": "полгода", "year": "год"}
LEVEL = {"high": "high", "medium": "mid", "low": "low", "no_score": "none", "churned": "gone"}
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def eur(cents: float) -> float:
    return round(cents / 100, 2)


def load():
    raw = json.loads((ROOT / "data" / "demo" / "data.json").read_text(encoding="utf-8"))
    return raw, SnapshotIn.model_validate(raw).to_snapshot()


def top_service(c):
    live = [r for r in c.services if r.score is not None]
    return max(live, key=lambda r: (r.score, r.expected_loss)) if live else (c.services[0] if c.services else None)


def plan(c) -> str:
    r = top_service(c)
    return f"{r.service.name} · {TERMS[r.service.paymentTerm]}" if r else ""


def rescore(raw: dict, mutate, account_id: int):
    """Пересчитать одного клиента после изменения данных (как пришедшее событие)."""
    data = copy.deepcopy(raw)
    mutate(data)
    r = evaluate(SnapshotIn.model_validate(data).to_snapshot(), default_settings())
    return r.by_id[account_id]


def main() -> None:
    raw, snap = load()
    settings = default_settings()
    r = evaluate(snap, settings)
    s = r.summary
    by_id = r.by_id

    clients = []
    for c in r.customers:
        clients.append({
            "id": f"user-{c.account.id:04d}", "email": c.account.email, "level": LEVEL[c.level],
            "score": c.score, "threat": eur(c.expected_loss), "revenue": eur(c.revenue_horizon),
            "prob": round((c.score or 0) / 100, 4), "reason": c.top_reason or c.note or "",
            "balance": eur(c.account.balance), "plan": plan(c),
        })

    scored = [c for c in r.customers if c.score is not None]
    generated = [c for c in scored if c.account.id > 10]  # клиенты кейса со странными почасовыми ценами — не для витрины

    def first_with(level, feature, exclude=()):
        pool = sorted((c for c in generated if c.level == level and c.account.id not in exclude
                       and c.contributions and c.contributions[0].feature == feature),
                      key=lambda c: -c.expected_loss)
        return pool[0] if pool else None

    pinned = first_with("high", "prolong_wait")
    pin_hours = round((top_service(pinned).days_left + 5) * 24)

    # Событие на радаре: клиенту не хватает денег, он пополняет счёт на недостающую сумму.
    ev = first_with("medium", "insufficient_funds") or first_with("high", "insufficient_funds")
    deficit = ev.renewal_need - ev.account.available_funds

    def topup(data, acc=ev.account.id, amount=deficit):
        a = next(x for x in data["accounts"] if x["id"] == acc)
        a["balance"] += amount
    ev_after = rescore(raw, topup, ev.account.id)

    # Рейтинг: 5 клиентов под риском с разными причинами.
    rank, seen = [], set()
    for c in sorted(generated, key=lambda c: -(c.score or 0)):
        f = c.contributions[0].feature if c.contributions else None
        if c.level in ("high", "medium") and f not in seen:
            seen.add(f)
            rank.append({"id": f"user-{c.account.id:04d}", "score": c.score, "bal": eur(c.account.balance),
                         "why": c.top_reason})
        if len(rank) == 5:
            break

    # Живое обновление: три настоящих события и пересчёт после них.
    live = [{"id": f"user-{ev.account.id:04d}", "why": ev.top_reason, "a": ev.score, "b": ev_after.score,
             "why2": "Счёт пополнен"}]
    low_ap = next(c for c in generated if c.level == "low" and any(
        r.service.autoProlong and r.service.status == "active" and 0 < r.days_left < 10 for r in c.services))
    svc = next(r.service for r in low_ap.services
               if r.service.autoProlong and r.service.status == "active" and 0 < r.days_left < 10)

    def ap_off(data, sid=svc.id):
        next(x for x in data["services"] if x["id"] == sid)["autoProlong"] = False
    after = rescore(raw, ap_off, low_ap.account.id)
    live.append({"id": f"user-{low_ap.account.id:04d}", "why": low_ap.top_reason, "a": low_ap.score,
                 "b": after.score, "why2": "Автопродление выключено"})
    def suspend(data, sid):
        next(x for x in data["services"] if x["id"] == sid)["status"] = "suspended"
    for low_act in (c for c in generated if c.level == "low" and c.account.id != low_ap.account.id
                    and all(r.service.status == "active" for r in c.services)):
        sid = low_act.services[0].service.id
        after2 = rescore(raw, lambda d, sid=sid: suspend(d, sid), low_act.account.id)
        if after2.score != low_act.score:
            break
    live.append({"id": f"user-{low_act.account.id:04d}", "why": low_act.top_reason, "a": low_act.score,
                 "b": after2.score, "why2": "Сервер выключен клиентом"})

    # Предпросмотр настройки: первый признак, у которого рост веса реально меняет число клиентов высокого риска.
    titles = {f.key: f.title for f in FEATURES}
    tune = None
    for key, to in [("insufficient_funds", 55), ("expiring_no_autoprolong", 50), ("no_autoprolong_no_funds", 45),
                    ("unpaid_transactions", 35), ("suspended", 40)]:
        tuned = settings.model_copy(deep=True)
        tuned.features[key].weight = to
        rt = evaluate(snap, tuned)
        if rt.summary.levels["high"] > s.levels["high"]:
            tune = {"feature": titles[key], "from": int(settings.features[key].weight), "to": to,
                    "highFrom": s.levels["high"], "highTo": rt.summary.levels["high"],
                    "lossDelta": eur(rt.summary.expected_loss - s.expected_loss)}
            break
    assert tune, "ни одна настройка не меняет высокий риск — подберите кандидатов"

    # Формула: 6 клиентов, сила сигналов главной услуги по трём признакам.
    keys = ["prolong_wait", "insufficient_funds", "expiring_no_autoprolong"]
    formula_pool = sorted((c for c in generated if c.level in ("high", "medium", "low") and c.contributions),
                          key=lambda c: -(c.score or 0))
    # по два клиента на главную причину — чтобы веса двигали разные строки
    picks, per_feature = [], {}
    for c in formula_pool:
        t = top_service(c)
        sig = tuple(round(next((x.strength for x in t.contributions if x.feature == k), 0), 2) for k in keys)
        main = t.contributions[0].feature if t.contributions else None
        if not any(sig) or per_feature.get(main, 0) >= 2:
            continue
        per_feature[main] = per_feature.get(main, 0) + 1
        other = sum(x.points for x in t.contributions if x.feature not in keys)
        picks.append({"id": f"user-{c.account.id:04d}", "s": list(sig), "other": round(other, 1),
                      "rev": eur(t.revenue_horizon)})
        if len(picks) == 6:
            break
    weights = {f.key: f.default_weight for f in FEATURES}

    # График: 9 дней факта и 5 дней прогноза вокруг даты расчёта, € в день.
    pts = timeline(r, snap, [])
    today = r.as_of.date().isoformat()
    i = next(k for k, p in enumerate(pts) if p.date == today)
    fact = [round(p.fact_recorded_eur + p.fact_restored_eur, 2) for p in pts[i - 8:i + 1]]
    fc = [round(p.forecast_eur, 2) for p in pts[i + 1:i + 6]]
    labels = []
    for p in pts[i - 8:i + 6]:
        d = int(p.date[8:]); m = MONTHS[int(p.date[5:7]) - 1]
        labels.append(f"{d} {m}" if p.date == today else str(d))

    # История «5 дней»: клиент, которому не хватает денег.
    story_c = ev
    story_svc = top_service(story_c).service
    story = {"service": story_svc.name, "price": eur(story_svc.price), "term": TERMS[story_svc.paymentTerm],
             "balance": eur(story_c.account.available_funds), "need": eur(story_c.renewal_need),
             "yearRevenue": eur(revenue_over_horizon(story_svc, 365)),
             "action": story_c.actions[0] if story_c.actions else ""}

    out = {
        "asOf": r.as_of.isoformat(),
        "clients": clients,
        "counts": {LEVEL[k]: v for k, v in s.levels.items()},
        "total": eur(s.expected_loss), "worst": eur(s.worst_case),
        "revenueAtRisk": eur(s.revenue_at_risk), "balanceAtRisk": eur(s.balance_at_risk),
        "pinned": {"id": f"user-{pinned.account.id:04d}", "hours": pin_hours},
        "event": {"id": f"user-{ev.account.id:04d}", "from": ev.score, "to": ev_after.score,
                  "amount": eur(deficit)},
        "rank": rank, "live": live, "tune": tune, "formula": picks,
        "weights": {"wait": weights["prolong_wait"], "funds": weights["insufficient_funds"],
                    "exp": weights["expiring_no_autoprolong"], "unpaid": weights["unpaid_transactions"],
                    "suspended": weights["suspended"],
                    "others": [[f.title, f.default_weight] for f in FEATURES if f.key not in keys]},
        "chart": {"fact": fact, "forecast": fc, "labels": labels},
        "story": story,
    }
    target = ROOT / "web" / "src" / "design" / "landing-data.js"
    target.write_text(
        "// Сгенерировано tools/build_landing_data.py из data/demo/data.json — не править вручную.\n"
        f"window.OTTOK_DATA = {json.dumps(out, ensure_ascii=False, indent=1)};\n",
        encoding="utf-8", newline="\n",
    )
    print(target)
    print(json.dumps({k: out[k] for k in ("counts", "total", "worst", "pinned", "event", "tune", "story")},
                     ensure_ascii=False, indent=1))
    print("rank", out["rank"]); print("live", out["live"]); print("formula", out["formula"]); print("chart", out["chart"])


if __name__ == "__main__":
    main()
