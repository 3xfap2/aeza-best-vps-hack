"""Актуальный результат расчёта и рассылка обновлений.

Любое изменение (данные, события, настройки) заканчивается вызовом recalculate():
снимок перечитывается из базы, модель пересчитывается целиком, версия растёт,
подписчики SSE получают событие и перечитывают нужные экраны.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from psycopg_pool import ConnectionPool

from app import db
from app.domain import Snapshot
from app.scoring.engine import EngineResult, Transition, evaluate
from app.scoring.settings import ModelSettings, default_settings


@dataclass
class Current:
    version: int
    reason: str
    computed_at: datetime
    duration_ms: float
    settings_version: int
    snapshot: Snapshot
    result: EngineResult
    transitions: list[Transition]


class Broker:
    """Подписчики SSE: у каждого своя очередь в event loop приложения."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    @property
    def count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: str, data: dict) -> None:
        if self._loop is None:
            return
        message = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        self._loop.call_soon_threadsafe(self._fan_out, message)

    def _fan_out(self, message: str) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:  # медленный клиент: пропускаем, он всё равно перечитает по последнему событию
                pass


class AppState:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool
        self.broker = Broker()
        self.recalculations = 0
        # Изменения данных и настроек выполняются по одному: чтение → запись → пересчёт.
        self.write_lock = threading.RLock()
        self._lock = threading.Lock()
        self._current: Current | None = None

    @property
    def current(self) -> Current:
        if self._current is None:
            raise RuntimeError("Расчёт ещё не выполнен")
        return self._current

    def settings(self) -> tuple[ModelSettings, int]:
        with self.pool.connection() as conn:
            found = db.current_settings(conn)
        return found if found else (default_settings(), 0)

    def recalculate(self, reason: str) -> Current:
        with self._lock:
            started = time.perf_counter()
            with self.pool.connection() as conn:
                snapshot = db.load_snapshot(conn)
                found = db.current_settings(conn)
                transitions = db.transitions(conn)
            settings, settings_version = found if found else (default_settings(), 0)
            result = evaluate(snapshot, settings)
            version = (self._current.version + 1) if self._current else 1
            self._current = Current(
                version=version,
                reason=reason,
                computed_at=datetime.now(timezone.utc),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                settings_version=settings_version,
                snapshot=snapshot,
                result=result,
                transitions=transitions,
            )
            self.recalculations += 1
        s = result.summary
        self.broker.publish("recalculated", {
            "version": version,
            "reason": reason,
            "computedAt": self._current.computed_at.isoformat(),
            "asOf": s.as_of.isoformat(),
            "expectedLossEur": round(s.expected_loss / 100, 2),
            "levels": s.levels,
        })
        return self._current
