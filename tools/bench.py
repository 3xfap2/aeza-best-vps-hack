#!/usr/bin/env python3
"""Замер скорости пересчёта модели на больших наборах.

    cd api && python ../tools/bench.py 1000 10000 50000

Данные — тот же генератор, что для расширенного набора (без клиентов кейса).
Меряется чистый расчёт модели (evaluate) — то, что выполняется при каждом изменении.
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
from app.scoring.engine import evaluate  # noqa: E402
from app.scoring.settings import default_settings  # noqa: E402
from app.source import SnapshotIn  # noqa: E402

REF = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)


def main(sizes: list[int]) -> None:
    print("| Клиентов | Услуг | Транзакций | Пересчёт, с | Память расчёта, МБ |")
    print("|---:|---:|---:|---:|---:|")
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
        print(f"| {n:,} | {len(snap.services):,} | {len(snap.transactions):,} | {min(runs):.2f} | {peak:.0f} |".replace(",", " "))


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]] or [1000, 10000, 50000])
