"""Быстрые ответы Радара — без ИИ.

Частые вопросы маркетолога Радар отвечает сам, из того же расчёта, что панель: мгновенно, без модели
и без риска выдуманных чисел. Работает, даже если ИИ не подключён; свободные вопросы — к модели (ai.ask).
"""
from __future__ import annotations

from app import backtest, db
from app.insights import what_if
from app.money import fmt_cents, fmt_euros
from app.retention import compare_strategies
from app.retention import queue as retention_queue
from app.scoring.engine import LEVEL_TITLES
from app.source import SnapshotIn

DEADLINE = {"overdue": "удаление задержано", "deletion": "удалится {:%d.%m %H:%M}", "renewal": "продление {:%d.%m}"}


def _pct(part: float, whole: float) -> str:
    return f"{round(part / whole * 100) if whole else 0} %"


def loss(radar) -> tuple[str, list[int]]:
    s = radar.current.result.summary
    lines = [f"Под угрозой за {s.horizon_days} дн.: {fmt_cents(s.expected_loss)} по прайсу, реально собираемо "
             f"{fmt_cents(s.expected_loss_paid)} — почасовые тарифы по прайсу клиенты столько не платят.",
             f"Высокий риск: {s.levels['high']} кл., средний: {s.levels['medium']} кл.", "",
             "Из чего складывается (по главной причине):"]
    for g in s.causes[:4]:
        kind = "не прошла оплата" if g["kind"] == "involuntary" else "решение клиента"
        lines.append(f"• {g['title']} — {g['customers']} кл., {fmt_cents(g['expected_loss_paid'])} реально ({kind})")
    return "\n".join(lines), []


def today(radar) -> tuple[str, list[int]]:
    cur = radar.current
    with radar.pool.connection() as conn:
        last, first = db.last_actions(conn), db.first_contacts(conn)
    items = retention_queue(cur.result, last, 30, 3, 10, first)
    plan = [x for x in items if x["planned"]] or items[:5]
    if not plan:
        return "Сегодня звонить некому: клиентов высокого и среднего риска нет.", []
    lines = [f"План на сегодня: {len(plan)} звонков, ожидаемо сохраним {fmt_euros(sum(x['expectedSaved'] for x in plan))}."
             " Порядок — по сроку, когда деньги пропадут безвозвратно, внутри срока — по выгоде.", ""]
    for n, x in enumerate(plan[:5], 1):
        when = DEADLINE[x["deadlineKind"]].format(x["deadline"])
        lines.append(f"{n}. клиент #{x['customerId']} — {when}, «{x['causeTitle']}», сохраним {fmt_euros(x['expectedSaved'])}. "
                     f"{x['action']}")
    if len(plan) > 5:
        lines.append(f"…и ещё {len(plan) - 5} — в «Удержании».")
    st = {x["key"]: x for x in compare_strategies(items, 10)}
    lines += ["", f"Почему такой порядок: при 10 звонках в день за неделю план сохранит {fmt_euros(st['plan']['saved'])}, "
                  f"а список «по риску» — {fmt_euros(st['score']['saved'])} и упустит {fmt_euros(st['score']['missed'])}: "
                  "у кого-то срок пройдёт раньше, чем дойдёт очередь."]
    return "\n".join(lines), [x["customerId"] for x in plan[:5]]


def customer(radar, customer_id: int) -> tuple[str, list[int]]:
    cur = radar.current
    c = cur.result.by_id.get(customer_id)
    if c is None:
        return f"Клиента #{customer_id} нет в данных.", []
    if c.score is None:
        return f"Клиент #{customer_id}: {LEVEL_TITLES[c.level].lower()}. {c.note or ''}", [customer_id]
    lines = [f"Клиент #{customer_id}: {LEVEL_TITLES[c.level].lower()} риск, score {c.score}. Потеря за "
             f"{cur.result.settings.horizonDays} дн.: {fmt_cents(c.expected_loss)} по прайсу, {fmt_cents(c.expected_loss_paid)} реально.",
             "", "Почему (баллы = вес × сила сигнала):"]
    top = list({x.feature: x for x in reversed(c.contributions)}.values())[::-1][:3]   # признак — один раз, с наибольшим вкладом
    lines += [f"• +{x.points:g} {x.title}: {x.reason}".replace(".", ",", 1) for x in top]
    if c.actions:
        lines += ["", "Что сделать: " + "; ".join(c.actions[:2]) + "."]
    scenarios = sorted(what_if(cur.result, cur.snapshot, customer_id), key=lambda w: -w["saved"])
    if scenarios:
        w = scenarios[0]
        lines.append(f"Лучший сценарий: {w['title'].lower()} → score {w['score']}, сохраним {fmt_euros(w['saved'])}.")
    return "\n".join(lines), [customer_id]


def accuracy(radar) -> tuple[str, list[int]]:
    cur = radar.current
    with radar.pool.connection() as conn:
        raw = db.history(conn)
    history = [SnapshotIn.model_validate(r).to_snapshot() for r in raw]
    b = backtest.run(history, cur.snapshot, cur.result.as_of, cur.result.settings)
    if not b.rounds:
        return ("Сверить пока не с чем: нет прошлых снимков хотя бы за горизонт. Каждый снимок биллинга с датой "
                "сохраняется сам — сверка появится в «Модели».", [])
    dates = ", ".join(f"{r.as_of:%d.%m}" for r in b.rounds)
    hi, lo = b.levels["high"], b.levels["low"]
    lines = [f"На прошлых снимках ({dates}) модель с нынешними весами заранее отметила {b.caught} из {b.churned} "
             f"клиентов, не продливших услугу ({_pct(b.caught, b.churned)}); ложных тревог: {b.false_alarms}.",
             f"Высокий риск: не продлили {_pct(hi.churned, hi.customers)} при прогнозе {round(hi.predicted * 100)} %; "
             f"низкий: {_pct(lo.churned, lo.customers)} при {round(lo.predicted * 100)} %."]
    hints = [f for f in b.features if f.verdict in ("under", "over")]
    if hints:
        lines += ["", "Подсказки по весам:"]
        for f in hints[:3]:
            verb = "поднять" if f.verdict == "under" else "снизить"
            lines.append(f"• {verb} «{f.title}» (сейчас {f.weight:g}): факт {_pct(f.churned, f.services)}, "
                         f"прогноз {round(f.predicted * 100)} %")
    return "\n".join(lines), []


def changes(radar) -> tuple[str, list[int]]:
    with radar.pool.connection() as conn:
        items = db.recent_activity(conn, 6)
    if not items:
        return "Изменений пока не было.", []
    lines = ["Последние изменения данных и модели:"]
    lines += [f"• {a['createdAt']:%d.%m %H:%M} — {a['summary']} ({a['author']})" for a in items]
    return "\n".join(lines), []
