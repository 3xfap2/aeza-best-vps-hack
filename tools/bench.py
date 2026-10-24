#!/usr/bin/env python3
"""Замер скорости пересчёта модели на больших наборах.

    cd api && python ../tools/bench.py 1000 10000 50000

Данные — тот же генератор, что для расширенного набора (без клиентов кейса).
Меряется чистый расчёт модели: полный (evaluate) — при смене настроек и загрузке снимка;
только затронутых клиентов (reevaluate) — при событии биллинга и пачке событий из очереди.
"""
from __future__ import annotations

import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "tools"))

from gen_demo import Gen  # noqa: E402
from app.scoring.engine import evaluate, reevaluate  # noqa: E402
from app.scoring.settings import default_settings  # noqa: E402
from app.source import SnapshotIn  # noqa: E402

REF = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)


def main(sizes: list[int]) -> None:
    print("| Клиентов | Услуг | Транзакций | Полный пересчёт, с | Память, МБ | Событие (1 клиент), мс | Пачка (2000 клиентов), мс |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    for n in sizes:
        data = Gen(42, REF).build(n)
        snap = SnapshotIn.model_validate(data).to_snapshot()
        settings = default_settings()
        evaluate(snap, settings)  # прогрев
        runs = []
        for _ in range(3):
            t = time.perf_counter()
            evaluate(snap, settings)
            runs.append(time.perf_counter() - t)
        # память — отдельным прогоном: tracemalloc сам замедляет расчёт в разы
        tracemalloc.start()
        evaluate(snap, settings)
        peak = tracemalloc.get_traced_memory()[1] / 1e6
        tracemalloc.stop()
        result = evaluate(snap, settings)
        one = _partial(result, snap, [snap.accounts[n // 2]])
        batch = _partial(result, snap, snap.accounts[:2000])
        print(f"| {n:,} | {len(snap.services):,} | {len(snap.transactions):,} | {min(runs):.2f} | {peak:.0f} "
              f"| {one * 1000:.0f} | {batch * 1000:.0f} |".replace(",", " "))


def _partial(result, snap, accounts) -> float:
    """Пересчёт только этих клиентов (их строки уже прочитаны из базы, как в API)."""
    ids = {a.id for a in accounts}
    services = [s for s in snap.services if s.ownerId in ids]
    sids = {s.id for s in services}
    transactions = [t for t in snap.transactions if t.serviceId in sids]
    runs = []
    for _ in range(3):
        t = time.perf_counter()
        reevaluate(result, accounts, services, transactions)
        runs.append(time.perf_counter() - t)
    return min(runs)


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]] or [1000, 10000, 50000])
