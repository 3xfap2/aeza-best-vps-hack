"""Сверка модели с исходами — обратная связь.

Прошлые снимки оцениваются текущими весами (как будто модель работала тогда), а затем сравниваются
с тем, что случилось за горизонт: услуга не продлена (удалена, ждёт оплаты, заблокирована) или продлена.
Так видно, сколько уходов модель предсказала заранее, сколько было ложных тревог и какие признаки
модель недооценивает или переоценивает. Меняете веса — сверка пересчитывается на той же истории.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain import Snapshot
from app.scoring.engine import evaluate, revenue_over_horizon
from app.scoring.features import FEATURES
from app.scoring.settings import AsOf, ModelSettings

NOT_RENEWED = frozenset({"deleted", "prolong_wait", "blocked"})
# Признак «ошибается», если доля непродлений расходится с прогнозом модели больше чем на 15 п. п.
MISMATCH = 0.15
MIN_SERVICES = 5


@dataclass
class Round:
    as_of: datetime
    outcome_at: datetime
    customers: int
    churned: int
    caught: int


@dataclass
class LevelRow:
    level: str
    customers: int = 0
    churned: int = 0
    predicted: float = 0.0          # сумма вероятностей, в конце — средняя


@dataclass
class FeatureRow:
    feature: str
    title: str
    weight: float
    services: int = 0
    churned: int = 0
    predicted: float = 0.0
    verdict: str = "ok"             # ok | under | over | few


@dataclass
class Backtest:
    horizon_days: int
    rounds: list[Round] = field(default_factory=list)
    levels: dict[str, LevelRow] = field(default_factory=dict)
    features: list[FeatureRow] = field(default_factory=list)
    customers: int = 0
    churned: int = 0
    caught: int = 0                 # ушедшие, которых модель заранее отнесла к высокому или среднему риску
    false_alarms: int = 0           # высокий риск, но услуги продлены
    predicted_loss: float = 0.0     # евроценты: ожидаемая потеря на даты прошлых снимков
    actual_loss: float = 0.0        # евроценты: выручка за горизонт по услугам, которые не продлили


def run(history: list[Snapshot], current: Snapshot, current_as_of: datetime, settings: ModelSettings) -> Backtest:
    horizon = settings.horizonDays
    out = Backtest(horizon, levels={lvl: LevelRow(lvl) for lvl in ("high", "medium", "low")})
    rows = {f.key: FeatureRow(f.key, f.title, settings.weight(f.key)) for f in FEATURES}
    # исход берём из первого снимка не раньше, чем через горизонт; последний кандидат — текущие данные
    outcomes = sorted([(s.reference_at, s) for s in history if s.reference_at] + [(current_as_of, current)],
                      key=lambda x: x[0])
    for snap in sorted((s for s in history if s.reference_at), key=lambda s: s.reference_at):
        t = snap.reference_at
        outcome = next((o for at, o in outcomes if at >= t + timedelta(days=horizon)), None)
        if outcome is None:
            continue
        status = {s.id: s.status for s in outcome.services}
        result = evaluate(snap, settings.model_copy(update={"asOf": AsOf(mode="fixed", value=t)}))
        rnd = Round(t, next(at for at, o in outcomes if o is outcome), 0, 0, 0)
        for c in result.customers:
            if c.score is None or c.level not in out.levels:
                continue
            lost_any = False
            for r in c.services:
                if r.score is None or r.service.id not in status:
                    continue
                lost = status[r.service.id] in NOT_RENEWED
                lost_any |= lost
                out.predicted_loss += r.expected_loss
                if lost:
                    out.actual_loss += revenue_over_horizon(r.service, horizon)
                for key in {x.feature for x in r.contributions}:
                    row = rows[key]
                    row.services += 1
                    row.churned += lost
                    row.predicted += r.probability
            lvl = out.levels[c.level]
            lvl.customers += 1
            lvl.churned += lost_any
            lvl.predicted += (c.score or 0) / 100
            rnd.customers += 1
            rnd.churned += lost_any
            rnd.caught += lost_any and c.level in ("high", "medium")
            out.false_alarms += (not lost_any) and c.level == "high"
        out.rounds.append(rnd)

    for lvl in out.levels.values():
        lvl.predicted = lvl.predicted / lvl.customers if lvl.customers else 0.0
    for row in rows.values():
        if not row.services:
            continue
        row.predicted /= row.services
        actual = row.churned / row.services
        row.verdict = ("few" if row.services < MIN_SERVICES else "under" if actual - row.predicted > MISMATCH
                       else "over" if row.predicted - actual > MISMATCH else "ok")
        out.features.append(row)
    out.features.sort(key=lambda r: -r.services)
    out.customers = sum(r.customers for r in out.rounds)
    out.churned = sum(r.churned for r in out.rounds)
    out.caught = sum(r.caught for r in out.rounds)
    return out
