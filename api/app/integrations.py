"""Отправка событий Радара во внешние системы: CRM, тикет-систему, свой сервис.

Радар публикует два события:
- feedback.created — сотрудник оставил отзыв о Радаре (идея, проблема);
- customer.action — маркетолог отметил работу с клиентом (позвонили, продлил, ушёл с причиной, перенос платежа).

Из коробки есть вебхук: INTEGRATION_WEBHOOK_URL — событие уходит JSON-ом POST-запросом,
с подписью X-Radar-Signature: sha256=HMAC(INTEGRATION_WEBHOOK_SECRET, тело), если секрет задан.

Подключить CRM напрямую: написать класс с методом send(event) — как WebhookSink ниже —
и добавить его в build_sinks(). Отправка идёт в фоне: внешняя система может тормозить или падать,
а действие в панели уже сохранено в базе и не ждёт её.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import httpx

log = logging.getLogger("radar.integrations")


@dataclass(frozen=True)
class Event:
    type: str
    data: dict
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> bytes:
        return json.dumps({**asdict(self), "at": self.at.isoformat()}, ensure_ascii=False, default=str).encode()


class Sink(Protocol):
    """Внешняя система, которая принимает события Радара."""
    name: str

    def send(self, event: Event) -> None: ...


class WebhookSink:
    name = "webhook"

    def __init__(self, url: str, secret: str | None = None, timeout: float = 5.0) -> None:
        self.url, self.secret, self.timeout = url, secret, timeout

    def send(self, event: Event) -> None:
        body = event.to_json()
        headers = {"Content-Type": "application/json", "X-Radar-Event": event.type}
        if self.secret:
            headers["X-Radar-Signature"] = "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        httpx.post(self.url, content=body, headers=headers, timeout=self.timeout).raise_for_status()


def build_sinks(env: dict) -> list[Sink]:
    sinks: list[Sink] = []
    if env.get("INTEGRATION_WEBHOOK_URL"):
        sinks.append(WebhookSink(env["INTEGRATION_WEBHOOK_URL"], env.get("INTEGRATION_WEBHOOK_SECRET") or None))
    return sinks


class Publisher:
    """Раздаёт событие всем подключённым системам в фоне; ошибка одной не мешает другим."""

    def __init__(self, sinks: list[Sink]) -> None:
        self.sinks = sinks
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="integrations") if sinks else None

    def publish(self, type_: str, data: dict) -> None:
        if not self._pool:
            return
        event = Event(type_, data)
        for sink in self.sinks:
            self._pool.submit(self._deliver, sink, event)

    @staticmethod
    def _deliver(sink: Sink, event: Event) -> None:
        try:
            sink.send(event)
        except Exception:  # внешняя система недоступна — событие уже сохранено в базе Радара
            log.exception("Не удалось отправить %s в %s", event.type, sink.name)

    def close(self) -> None:
        if self._pool:
            self._pool.shutdown(wait=False)
