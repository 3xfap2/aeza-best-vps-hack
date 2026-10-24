"""Обработка очереди событий биллинга (event_inbox), которую наполняет сервис приёма на Go.

Событие применяется тем же путём, что POST /api/v1/source/events, но пачкой: до BATCH событий
в одной транзакции и один пересчёт на пачку — только затронутых клиентов. Ошибочное событие
не ломает пачку: оно помечается ok = false с причиной, остальные применяются.
Будит обработчик уведомление radar_inbox (LISTEN/NOTIFY); раз в секунду — проверка на случай пропуска.
"""
from __future__ import annotations

import logging
import threading
import time

import psycopg
from pydantic import ValidationError

from app import db, events, simulator

log = logging.getLogger("radar.inbox")

BATCH = 2000
AUTHOR = "биллинг (поток)"


class Inbox:
    def __init__(self, radar, database_url: str) -> None:
        self.radar = radar
        self.url = database_url
        self.processed = 0
        self.failed = 0
        self.batches = 0
        self._pruned = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="inbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with psycopg.connect(self.url, autocommit=True) as listen:
                    listen.execute("LISTEN radar_inbox")
                    while not self._stop.is_set():
                        self.drain()
                        if time.time() - self._pruned > 3600:   # обработанные события храним неделю
                            with self.radar.pool.connection() as conn:
                                db.prune_inbox(conn)
                            self._pruned = time.time()
                        for _ in listen.notifies(timeout=1.0, stop_after=1):
                            pass
            except Exception:  # база перезапускается — подождём и переподключимся
                log.exception("Очередь событий: ошибка, переподключение")
                self._stop.wait(3)

    def drain(self) -> int:
        """Обработать всё, что накопилось; возвращает число событий."""
        total = 0
        while True:
            n = self._batch()
            total += n
            if n < BATCH:
                return total

    def _batch(self) -> int:
        radar = self.radar
        with radar.write_lock:
            started = time.perf_counter()
            at = radar.current.result.as_of
            affected: set[int] = set()
            marks: list[tuple[bool, str, int]] = []
            with radar.pool.connection() as conn:
                rows = conn.execute(
                    "SELECT id, payload FROM event_inbox WHERE processed_at IS NULL ORDER BY id LIMIT %s "
                    "FOR UPDATE SKIP LOCKED", (BATCH,)).fetchall()
                if not rows:
                    return 0
                for r in rows:
                    try:
                        with conn.transaction():
                            event = events.event_adapter.validate_python(r["payload"])
                            summary = events.write(conn, event, at)
                            account = events.affected_account(conn, event)
                        if account:
                            affected.add(account)
                        marks.append((True, summary, r["id"]))
                    except (ValidationError, simulator.SimulationError, psycopg.Error) as e:
                        reason = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
                        marks.append((False, reason[:300], r["id"]))
                with conn.cursor() as cur:
                    cur.executemany("UPDATE event_inbox SET processed_at = now(), ok = %s, result = %s WHERE id = %s", marks)
                ok = sum(1 for m in marks if m[0])
                db.log_activity(conn, "event", f"События из биллинга пачкой: применено {ok}"
                                + (f", отклонено {len(marks) - ok}" if ok < len(marks) else "")
                                + f"; клиентов затронуто: {len(affected)}", AUTHOR,
                                {"first": rows[0]["id"], "last": rows[-1]["id"]})
            if affected:
                radar.recalculate("ingest", accounts=affected)
            self.processed += ok
            self.failed += len(marks) - ok
            self.batches += 1
            log.info("Очередь событий: пачка %d (ошибок %d) за %.0f мс", len(marks), len(marks) - ok,
                     (time.perf_counter() - started) * 1000)
            return len(rows)


def pending(conn) -> int:
    return conn.execute("SELECT count(*) AS n FROM event_inbox WHERE processed_at IS NULL").fetchone()["n"]
