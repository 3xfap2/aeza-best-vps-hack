"""Живой поток событий (демо): Радар сам присылает себе правдоподобные события биллинга.

Нужен, чтобы стенд «жил» без настоящего биллинга: суммы меняются, клиенты переходят между
уровнями, бот присылает уведомления. События идут тем же путём, что POST /api/v1/source/events,
и помечаются в журнале автором «демо-поток»; «Вернуть к дате снимка» всё откатывает.

Выбор события сбалансирован: примерно поровну «спасают» клиента (пополнение, оплата,
включение автопродления) и «портят» (выключение автопродления, падение нагрузки, тикеты),
чтобы картина не уезжала в одну сторону.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

from app.scoring.engine import EngineResult

log = logging.getLogger("radar.demo")
AUTHOR = "демо-поток"


def pick_event(result: EngineResult, rng: random.Random) -> dict | None:
    """Одно событие в формате POST /source/events по текущему расчёту."""
    risky = [c for c in result.customers if c.level in ("high", "medium") and c.score is not None]
    calm = [c for c in result.customers if c.level == "low"]
    live = lambda cs: [r.service for c in cs for r in c.services if r.score is not None]  # noqa: E731

    roll = rng.random()
    if roll < 0.20 and risky:          # клиент пополнил счёт на нехватку (или часть)
        c = rng.choice(risky)
        need = max(c.renewal_need - c.account.available_funds, 500)
        return {"type": "topup", "accountId": c.account.id, "amount": round(need / 100 * rng.uniform(0.5, 1.2), 2)}
    if roll < 0.35 and risky:          # оплатил ожидающую услугу, если хватает денег
        waiting = [r.service for c in risky for r in c.services
                   if r.service.status == "prolong_wait" and c.account.available_funds >= r.service.price]
        if waiting:
            return {"type": "pay_renewal", "serviceId": rng.choice(waiting).id}
    if roll < 0.50:                    # включил автопродление
        off = [s for s in live(risky or calm) if not s.autoProlong]
        if off:
            return {"type": "autoprolong", "serviceId": rng.choice(off).id, "enabled": True}
    if roll < 0.75 and calm:           # спокойный клиент выключил автопродление перед сроком
        on = [s for s in live(calm) if s.autoProlong and s.status == "active"]
        if on:
            return {"type": "autoprolong", "serviceId": rng.choice(on).id, "enabled": False}
    if roll < 0.88:                    # мониторинг: нагрузка на сервер упала
        services = [s for s in live(calm or risky) if s.status == "active"]
        if services:
            return {"type": "signal", "name": "loadDrop", "targetId": rng.choice(services).id, "value": round(rng.uniform(0.6, 0.95), 2)}
    if calm or risky:                  # поддержка: несколько обращений за месяц
        c = rng.choice(calm or risky)
        return {"type": "signal", "name": "supportTickets30d", "targetId": c.account.id, "value": rng.randint(3, 6)}
    return None


class DemoStream:
    """Фоновый поток: раз в interval секунд — одно событие, до остановки или таймера."""

    def __init__(self, radar, send: Callable[[dict], str]) -> None:
        self.radar = radar
        self.send = send             # применяет событие тем же путём, что POST /source/events
        self.interval = 8
        self.stops_at: float | None = None
        self.sent = 0
        self.last: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rng = random.Random()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, interval: int, minutes: int) -> None:
        self.stop()
        self.interval, self.sent, self.last = interval, 0, None
        self.stops_at = time.time() + minutes * 60 if minutes else None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="demo-stream", daemon=True)
        self._thread.start()
        log.info("Демо-поток запущен: раз в %s с, %s", interval, f"{minutes} мин" if minutes else "без ограничения")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def tick(self) -> str | None:
        event = pick_event(self.radar.current.result, self._rng)
        if event is None:
            return None
        summary = self.send(event)
        self.sent += 1
        self.last = summary
        return summary

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            if self.stops_at and time.time() >= self.stops_at:
                log.info("Демо-поток остановлен по таймеру после %s событий", self.sent)
                return
            try:
                self.tick()
            except Exception:  # событие стало невозможным (данные поменялись) — просто берём следующее
                log.info("Демо-поток: событие пропущено", exc_info=True)
