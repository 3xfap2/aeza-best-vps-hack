"""Устойчивость модели к весам: что будет, если каждый вес ошибочен на ±20 %.

Для каждого признака вес сдвигается вниз и вверх, модель пересчитывается на тех же данных и той же дате.
Смотрим, сколько клиентов сменят уровень, насколько изменится ожидаемая потеря и сколько клиентов
останется в плане звонков на день. Если выводы почти не меняются — веса не «подогнаны» под ответ.
На больших базах считаем на детерминированной выборке клиентов (каждый k-й), это помечается.
"""
from __future__ import annotations

from app.domain import Snapshot
from app.retention import queue
from app.scoring.engine import EngineResult, evaluate
from app.scoring.features import FEATURES
from app.scoring.settings import AsOf, ModelSettings

DELTA = 0.2
SAMPLE_LIMIT = 5000
PLAN_CALLS = 10


def _sample(snapshot: Snapshot, limit: int) -> tuple[Snapshot, bool]:
    if len(snapshot.accounts) <= limit:
        return snapshot, False
    step = -(-len(snapshot.accounts) // limit)
    ids = {a.id for a in snapshot.accounts[::step]}
    services = [s for s in snapshot.services if s.ownerId in ids]
    sids = {s.id for s in services}
    return Snapshot([a for a in snapshot.accounts if a.id in ids], services,
                    [t for t in snapshot.transactions if t.serviceId in sids], snapshot.reference_at), True


def _plan(result: EngineResult) -> set[int]:
    return {x["customerId"] for x in queue(result, {}, 30, 3, PLAN_CALLS, {}) if x["planned"]}


def _shift(settings: ModelSettings, key: str, factor: float) -> ModelSettings:
    data = settings.model_dump(mode="json")
    data["features"][key]["weight"] = round(min(100.0, max(0.0, data["features"][key]["weight"] * factor)), 2)
    return ModelSettings.model_validate(data)


def run(snapshot: Snapshot, result: EngineResult) -> dict:
    sub, sampled = _sample(snapshot, SAMPLE_LIMIT)
    settings = result.settings.model_copy(update={"asOf": AsOf(mode="fixed", value=result.as_of)})
    base = evaluate(sub, settings)
    levels = {c.account.id: c.level for c in base.customers}
    plan = _plan(base)
    loss = base.summary.expected_loss
    rows = []
    for f in FEATURES:
        st = settings.features[f.key]
        if not st.enabled or st.weight <= 0:
            continue
        row = {"feature": f.key, "title": f.title, "weight": st.weight}
        for name, factor in (("minus", 1 - DELTA), ("plus", 1 + DELTA)):
            r = evaluate(sub, _shift(settings, f.key, factor))
            row[name] = {
                "changed": sum(1 for c in r.customers if levels.get(c.account.id) != c.level),
                "lossPct": round((r.summary.expected_loss - loss) / loss * 100, 1) if loss else 0.0,
                "planSame": len(plan & _plan(r)),
            }
        rows.append(row)
    worst = [x for r in rows for x in (r["minus"], r["plus"])]
    return {
        "delta": DELTA, "sampled": sampled, "customers": len(base.customers),
        "scored": sum(1 for c in base.customers if c.score is not None), "planSize": len(plan), "rows": rows,
        "maxChanged": max((x["changed"] for x in worst), default=0),
        "maxLossPct": max((abs(x["lossPct"]) for x in worst), default=0.0),
        "minPlanSame": min((x["planSame"] for x in worst), default=len(plan)),
    }
