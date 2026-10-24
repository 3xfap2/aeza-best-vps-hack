"""Актуальный результат расчёта и рассылка обновлений.

Любое изменение (данные, события, настройки) заканчивается вызовом recalculate():
снимок перечитывается из базы, модель пересчитывается целиком, версия растёт,
подписчики SSE получают событие и перечитывают нужные экраны.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from psycopg_pool import ConnectionPool

from app import db
from app.integrations import Publisher
from app.domain import Snapshot
from app.scoring.engine import EngineResult, Transition, evaluate, reevaluate
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
        self.partial_recalculations = 0
        # Внешние системы (CRM, вебхук): события отзывов и работы с клиентами. По умолчанию никуда не уходят.
        self.integrations = Publisher([])
        # Вызываются после каждого пересчёта: (прошлый результат, новый результат, причина).
        self.listeners: list[Callable] = []
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

    def _record_history(self, previous: EngineResult | None, result: EngineResult, reason: str) -> None:
        """Пишем в историю только клиентов, у которых изменились score или уровень."""
        with self.pool.connection() as conn:
            before = ({c.account.id: (c.score, c.level) for c in previous.customers} if previous
                      else db.last_scores(conn))
            changed = [(c.account.id, c.score, c.level) for c in result.customers
                       if before.get(c.account.id) != (c.score, c.level)]
            db.save_score_changes(conn, changed, result.as_of, reason)

    def recalculate(self, reason: str, accounts: set[int] | None = None) -> Current:
        """Пересчёт. accounts — затронутые клиенты (события биллинга): пересчитываются только они,
        если настройки не менялись и дата расчёта не «сейчас»; иначе — полный пересчёт."""
        with self._lock:
            started = time.perf_counter()
            cur = self._current
            with self.pool.connection() as conn:
                found = db.current_settings(conn)
                settings, settings_version = found if found else (default_settings(), 0)
                partial = (accounts is not None and cur is not None and settings_version == cur.settings_version
                           and settings.asOf.mode != "now")
                if partial:
                    rows = db.load_accounts(conn, accounts)
                else:
                    snapshot = db.load_snapshot(conn)
                transitions = db.transitions(conn)
            if partial:
                snapshot = cur.snapshot.with_accounts(*rows)
                result = reevaluate(cur.result, *rows)
            else:
                result = evaluate(snapshot, settings)
            self.partial_recalculations += partial
            previous = self._current.result if self._current else None
            self._record_history(previous, result, reason)
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
        for listener in self.listeners:
            try:
                listener(previous, result, reason)
            except Exception:  # уведомления не должны ломать пересчёт
                logging.getLogger("radar").exception("Ошибка обработчика пересчёта")
        return self._current
