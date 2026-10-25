"""Отправка событий Радара во внешние системы: CRM, тикет-систему, свой сервис.

Радар публикует два события:
- feedback.created — сотрудник оставил отзыв о Радаре (идея, проблема);
- customer.action — маркетолог отметил работу с клиентом (позвонили, продлил, ушёл с причиной, перенос платежа).

Доставка надёжная (transactional outbox): событие пишется в таблицу outbox в той же транзакции,
что и действие, поэтому не теряется, если CRM недоступна или Радар перезапустился. Фоновый поток
доставляет его с повторами (пауза 5 с, 10 с, 20 с… до часа; после 10 попыток событие остаётся
в outbox с last_error — его видно в метриках). Получатель отличает повтор по X-Radar-Delivery.

Из коробки — вебхук: INTEGRATION_WEBHOOK_URL, событие уходит JSON-ом POST-запросом. Если задан
INTEGRATION_WEBHOOK_SECRET, запрос подписан по Standard Webhooks (https://www.standardwebhooks.com/):
webhook-id, webhook-timestamp, webhook-signature: v1,base64(HMAC-SHA256(секрет, "id.timestamp.тело")) —
отметка времени в подписи не даёт отправить перехваченный запрос повторно. Для совместимости остаётся
X-Radar-Signature: sha256=HMAC(секрет, тело).
Подключить CRM напрямую — класс с атрибутом name и методом send(event), как WebhookSink, и строка в build_sinks().
Что доставлено, что ждёт и что сдалось — GET /api/v1/integrations/deliveries; переотправить — .../redeliver.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx
from psycopg.types.json import Jsonb

log = logging.getLogger("radar.integrations")

MAX_ATTEMPTS = 10
BATCH = 50


@dataclass(frozen=True)
class Event:
    id: int          # номер в outbox — одинаковый при повторной доставке
    type: str
    data: dict
    at: datetime

    def to_json(self) -> bytes:
        return json.dumps({"id": self.id, "type": self.type, "data": self.data, "at": self.at.isoformat()},
                          ensure_ascii=False, default=str).encode()


class Sink(Protocol):
    """Внешняя система, которая принимает события Радара. Ошибка — исключение: доставка повторится."""
    name: str

    def send(self, event: Event) -> None: ...


class WebhookSink:
    name = "webhook"

    def __init__(self, url: str, secret: str | None = None, timeout: float = 5.0) -> None:
        self.url, self.secret, self.timeout = url, secret, timeout

    def send(self, event: Event) -> None:
        body = event.to_json()
        headers = {"Content-Type": "application/json", "X-Radar-Event": event.type, "X-Radar-Delivery": str(event.id)}
        if self.secret:
            headers.update(sign(self.secret, str(event.id), int(time.time()), body))
            headers["X-Radar-Signature"] = "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        httpx.post(self.url, content=body, headers=headers, timeout=self.timeout).raise_for_status()


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> dict[str, str]:
    """Заголовки подписи Standard Webhooks. Секрет вида whsec_<base64> — как у Svix, иначе ключ — байты строки."""
    key = base64.b64decode(secret.removeprefix("whsec_")) if secret.startswith("whsec_") else secret.encode()
    digest = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return {"webhook-id": msg_id, "webhook-timestamp": str(timestamp),
            "webhook-signature": "v1," + base64.b64encode(digest).decode()}


def build_sinks(env: dict) -> list[Sink]:
    sinks: list[Sink] = []
    if env.get("INTEGRATION_WEBHOOK_URL"):
        sinks.append(WebhookSink(env["INTEGRATION_WEBHOOK_URL"], env.get("INTEGRATION_WEBHOOK_SECRET") or None))
    return sinks


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)   # даты — строками ISO


def backoff(attempts: int) -> timedelta:
    """Пауза перед следующей попыткой: 5 с, 10 с, 20 с… не больше часа."""
    return timedelta(seconds=min(5 * 2 ** (attempts - 1), 3600))


class Outbox:
    def __init__(self, sinks: list[Sink], pool) -> None:
        self.sinks = {s.name: s for s in sinks}
        self.pool = pool
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue(self, conn, type_: str, data: dict) -> None:
        """Записать событие для каждой подключённой системы — в транзакции вызывающего кода."""
        for name in self.sinks:
            conn.execute("INSERT INTO outbox (sink, type, payload) VALUES (%s, %s, %s)", (name, type_, Jsonb(data, dumps=_dumps)))

    def deliver_due(self) -> int:
        """Доставить события, у которых подошло время попытки. Возвращает число успешных доставок."""
        if not self.sinks:
            return 0
        delivered = 0
        with self.pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                "SELECT id, sink, type, payload, created_at, attempts FROM outbox "
                "WHERE delivered_at IS NULL AND attempts < %s AND next_attempt_at <= now() "
                "ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED", (MAX_ATTEMPTS, BATCH)).fetchall()
            for r in rows:
                sink = self.sinks.get(r["sink"])
                try:
                    if sink is None:
                        raise RuntimeError(f"Система «{r['sink']}» больше не подключена")
                    sink.send(Event(r["id"], r["type"], r["payload"], r["created_at"]))
                except Exception as e:  # внешняя система недоступна — повторим позже
                    attempts = r["attempts"] + 1
                    conn.execute("UPDATE outbox SET attempts = %s, next_attempt_at = %s, last_error = %s WHERE id = %s",
                                 (attempts, datetime.now(timezone.utc) + backoff(attempts), str(e)[:500], r["id"]))
                    log.warning("Событие %s → %s не доставлено (попытка %s): %s", r["type"], r["sink"], attempts, e)
                    continue
                conn.execute("UPDATE outbox SET delivered_at = now(), attempts = attempts + 1, last_error = NULL "
                             "WHERE id = %s", (r["id"],))
                delivered += 1
        return delivered

    def start(self, interval: float = 2.0) -> None:
        if not self.sinks:
            return
        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.deliver_due()
                except Exception:  # база перезапускается — следующий цикл повторит
                    log.exception("Доставка outbox не удалась")
        self._thread = threading.Thread(target=loop, name="outbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def outbox_counts(conn) -> dict[str, int]:
    r = conn.execute(
        "SELECT count(*) FILTER (WHERE delivered_at IS NULL AND attempts < %s) AS pending, "
        "count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered, "
        "count(*) FILTER (WHERE delivered_at IS NULL AND attempts >= %s) AS failed FROM outbox",
        (MAX_ATTEMPTS, MAX_ATTEMPTS)).fetchone()
    return dict(r)


_DELIVERY = ("SELECT id, sink, type, payload, created_at, attempts, next_attempt_at, delivered_at, last_error, "
             "CASE WHEN delivered_at IS NOT NULL THEN 'delivered' WHEN attempts >= %(max)s THEN 'failed' "
             "ELSE 'pending' END AS status FROM outbox")


def list_deliveries(conn, status: str | None, limit: int) -> list[dict]:
    """Журнал доставок, новые сверху; status — delivered, pending или failed (сдалось после MAX_ATTEMPTS)."""
    return conn.execute(f"SELECT * FROM ({_DELIVERY}) d WHERE %(status)s::text IS NULL OR status = %(status)s "
                        "ORDER BY id DESC LIMIT %(limit)s",
                        {"max": MAX_ATTEMPTS, "status": status, "limit": limit}).fetchall()


def redeliver(conn, delivery_id: int) -> dict | None:
    """Поставить событие на отправку заново — сразу и с полным запасом попыток (доставленное тоже)."""
    found = conn.execute("UPDATE outbox SET attempts = 0, next_attempt_at = now(), delivered_at = NULL, "
                         "last_error = NULL WHERE id = %s RETURNING id", (delivery_id,)).fetchone()
    if found is None:
        return None
    return conn.execute(f"{_DELIVERY} WHERE id = %(id)s", {"max": MAX_ATTEMPTS, "id": delivery_id}).fetchone()
