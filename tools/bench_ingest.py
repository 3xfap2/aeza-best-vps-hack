#!/usr/bin/env python3
"""Замер потока событий на работающем стенде: по одному через API против пачек через сервис приёма (Go).

    INGEST_TOKEN=... ADMIN_PASSWORD=... python tools/bench_ingest.py [событий через Go] [событий через API]

Шлёт пополнения на € 1 случайным клиентам (риск от них только падает — бот не засыпает уведомлениями),
ждёт, пока очередь разберётся, и в конце возвращает данные к снимку (POST /api/v1/source/reset).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.request

BASE = os.environ.get("BASE_URL", "http://localhost:8080")
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, token: str, body=None) -> dict | str:
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with NO_PROXY.open(req, timeout=120) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw.startswith(("{", "[")) else raw


def pending(token: str) -> int:
    for line in call("GET", "/metrics", token).splitlines():
        if line.startswith('churn_radar_inbox_events{state="pending"}'):
            return int(float(line.split()[-1]))
    return 0


def main(n_ingest: int, n_api: int) -> None:
    token = call("POST", "/api/v1/auth/login", "", {"login": os.environ.get("ADMIN_LOGIN", "admin"),
                                                  "password": os.environ.get("ADMIN_PASSWORD", "radar-demo-2026")})["token"]
    ingest = os.environ["INGEST_TOKEN"]
    ids = [c["id"] for c in call("GET", "/api/v1/customers?pageSize=200", token)["items"]]
    topup = lambda: {"type": "topup", "accountId": random.choice(ids), "amount": 1}  # noqa: E731

    t = time.perf_counter()
    for _ in range(n_api):
        call("POST", "/api/v1/source/events", token, topup())
    api_s = time.perf_counter() - t

    run = f"bench-{int(time.time())}"
    t = time.perf_counter()
    for start in range(0, n_ingest, 1000):
        batch = [{**topup(), "eventId": f"{run}-{i}"} for i in range(start, min(start + 1000, n_ingest))]
        call("POST", "/api/v1/ingest/events", ingest, batch)
    accept_s = time.perf_counter() - t
    while pending(token):
        time.sleep(0.2)
    total_s = time.perf_counter() - t
    call("POST", "/api/v1/source/reset", token, {})

    print("| Путь | Событий | Время, с | Событий в секунду |")
    print("|---|---:|---:|---:|")
    print(f"| По одному: POST /api/v1/source/events | {n_api} | {api_s:.1f} | {n_api / api_s:.0f} |")
    print(f"| Пачками по 1000: приём (Go, 202) | {n_ingest} | {accept_s:.1f} | {n_ingest / accept_s:.0f} |")
    print(f"| Пачками по 1000: до пересчёта всех | {n_ingest} | {total_s:.1f} | {n_ingest / total_s:.0f} |")


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:]]
    main(args[0] if args else 10000, args[1] if len(args) > 1 else 200)
