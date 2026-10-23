"""Telegram-бот маркетолога.

Подписка: /start <TELEGRAM_LINK_CODE>. Без кода бот ничего не показывает — в нём данные клиентов.
Уведомления: после каждого пересчёта — о клиентах, которые перешли в высокий риск.
Команды: /summary, /top, /client <id или email>, /stop.
Работает в фоновом потоке через long polling; сообщения отправляются из очереди,
поэтому пересчёт никогда не ждёт Telegram.
"""
from __future__ import annotations

import hmac
import html
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

import httpx

from app.scoring.engine import LEVEL_TITLES, CustomerResult, EngineResult
from app.views import eur

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger("radar.telegram")

MAX_ALERTS_PER_RECALC = 5
HELP = (
    "Команды:\n"
    "/summary — сколько денег под угрозой\n"
    "/top — 5 клиентов с наибольшей ожидаемой потерей\n"
    "/client 42 или /client user-0042@example.test — карточка клиента\n"
    "/stop — отключить уведомления"
)


def _money(cents: float) -> str:
    return f"€ {eur(cents):,.2f}".replace(",", " ").replace(".", ",")


class TelegramBot:
    def __init__(self, token: str, link_code: str, radar: "AppState", public_url: str) -> None:
        self.api = f"https://api.telegram.org/bot{token}"
        self.link_code = link_code
        self.radar = radar
        self.public_url = public_url
        self._outbox: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- жизненный цикл ------------------------------------------------------

    def start(self) -> None:
        for target, name in ((self._poll, "tg-poll"), (self._send_loop, "tg-send")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        self.radar.listeners.append(self.on_recalculated)
        log.info("Telegram-бот запущен")

    def stop(self) -> None:
        self._stop.set()

    # --- уведомления ---------------------------------------------------------

    def on_recalculated(self, before: EngineResult | None, after: EngineResult, reason: str) -> None:
        if before is None or reason == "startup":
            return
        old = {c.account.id: c.level for c in before.customers}
        escalated = [c for c in after.customers if c.level == "high" and old.get(c.account.id) != "high"]
        if not escalated:
            return
        escalated.sort(key=lambda c: c.expected_loss, reverse=True)
        lines = [f"<b>В высокий риск перешли: {len(escalated)}</b>"]
        for c in escalated[:MAX_ALERTS_PER_RECALC]:
            lines.append("")
            lines.append(self._customer_short(c, old.get(c.account.id)))
        if len(escalated) > MAX_ALERTS_PER_RECALC:
            lines.append(f"\nи ещё {len(escalated) - MAX_ALERTS_PER_RECALC} — в панели: {self.public_url}/customers")
        self._broadcast("\n".join(lines))

    def _broadcast(self, text: str) -> None:
        for chat_id in self._subscribers():
            try:
                self._outbox.put_nowait((chat_id, text))
            except queue.Full:
                log.warning("Очередь Telegram переполнена, сообщение пропущено")

    # --- форматирование ------------------------------------------------------

    def _customer_short(self, c: CustomerResult, from_level: str | None = None) -> str:
        was = f" (было: {LEVEL_TITLES[from_level].lower()})" if from_level else ""
        head = f"<b>{html.escape(c.account.email)}</b> · score {c.score} · {_money(c.expected_loss)}{was}"
        parts = [head, html.escape(c.top_reason or c.note or "")]
        if c.actions:
            parts.append(f"→ {html.escape(c.actions[0])}")
        parts.append(f"{self.public_url}/customers/{c.account.id}")
        return "\n".join(parts)

    def _summary(self) -> str:
        s = self.radar.current.result.summary
        lv = s.levels
        return (
            f"<b>Под угрозой за {s.horizon_days} дн.: {_money(s.expected_loss)}</b>\n"
            f"Худший случай: {_money(s.worst_case)}\n"
            f"Клиенты: высокий {lv['high']} · средний {lv['medium']} · низкий {lv['low']} · "
            f"нет оценки {lv['no_score']} · ушли {lv['churned']}\n"
            f"Данные на {s.as_of:%d.%m.%Y %H:%M} UTC"
        )

    def _top(self) -> str:
        result = self.radar.current.result
        top = sorted((c for c in result.customers if c.level in ("high", "medium")),
                     key=lambda c: c.expected_loss, reverse=True)[:5]
        if not top:
            return "Клиентов высокого и среднего риска нет."
        return "<b>Топ-5 по ожидаемой потере</b>\n\n" + "\n\n".join(self._customer_short(c) for c in top)

    def _client(self, arg: str) -> str:
        result = self.radar.current.result
        arg = arg.strip().lower()
        found = result.by_id.get(int(arg)) if arg.isdigit() else next(
            (c for c in result.customers if c.account.email.lower() == arg), None)
        if found is None:
            return "Клиент не найден. Пример: /client 42"
        lines = [self._customer_short(found), "", f"Уровень: {LEVEL_TITLES[found.level]}"]
        for x in found.contributions[:4]:
            lines.append(f"+{x.points:g} · {html.escape(x.title)}: {html.escape(x.reason)}")
        return "\n".join(lines)

    # --- подписчики ----------------------------------------------------------

    def _subscribers(self) -> list[int]:
        with self.radar.pool.connection() as conn:
            return [r["chat_id"] for r in conn.execute("SELECT chat_id FROM telegram_subscribers")]

    def _is_subscribed(self, chat_id: int) -> bool:
        with self.radar.pool.connection() as conn:
            return conn.execute("SELECT 1 FROM telegram_subscribers WHERE chat_id = %s", (chat_id,)).fetchone() is not None

    def _subscribe(self, chat_id: int, username: str | None) -> None:
        with self.radar.pool.connection() as conn:
            conn.execute(
                "INSERT INTO telegram_subscribers (chat_id, username) VALUES (%s, %s) ON CONFLICT (chat_id) DO NOTHING",
                (chat_id, username),
            )

    def _unsubscribe(self, chat_id: int) -> None:
        with self.radar.pool.connection() as conn:
            conn.execute("DELETE FROM telegram_subscribers WHERE chat_id = %s", (chat_id,))

    # --- обработка команд ----------------------------------------------------

    def handle(self, chat_id: int, username: str | None, text: str) -> str:
        command, _, arg = text.strip().partition(" ")
        command = command.split("@")[0].lower()
        if command == "/start":
            if hmac.compare_digest(arg.strip(), self.link_code):
                self._subscribe(chat_id, username)
                return "Готово: буду присылать клиентов, перешедших в высокий риск.\n\n" + HELP
            if self._is_subscribed(chat_id):
                return HELP
            return "Это бот «Радара оттока». Чтобы подключиться, отправьте /start и код доступа из настроек сервера."
        if not self._is_subscribed(chat_id):
            return "Сначала подключитесь: /start <код доступа>."
        if command == "/stop":
            self._unsubscribe(chat_id)
            return "Уведомления отключены. Вернуться: /start <код доступа>."
        if command == "/summary":
            return self._summary()
        if command == "/top":
            return self._top()
        if command == "/client":
            return self._client(arg) if arg.strip() else "Укажите ID или email: /client 42"
        return HELP

    # --- сеть ----------------------------------------------------------------

    def _poll(self) -> None:
        offset = 0
        with httpx.Client(timeout=40) as http:
            while not self._stop.is_set():
                try:
                    r = http.get(f"{self.api}/getUpdates", params={"timeout": 30, "offset": offset})
                    r.raise_for_status()
                    for update in r.json().get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message") or {}
                        text = msg.get("text")
                        if not text:
                            continue
                        chat = msg["chat"]
                        reply = self.handle(chat["id"], chat.get("username"), text)
                        self._outbox.put((chat["id"], reply))
                except Exception:  # сеть или Telegram недоступны — повторяем позже, сервер продолжает работать
                    log.warning("Telegram: ошибка опроса, повтор через 5 с", exc_info=True)
                    self._stop.wait(5)

    def _send_loop(self) -> None:
        with httpx.Client(timeout=15) as http:
            while not self._stop.is_set():
                try:
                    chat_id, text = self._outbox.get(timeout=1)
                except queue.Empty:
                    continue
                for attempt in range(3):
                    try:
                        r = http.post(f"{self.api}/sendMessage", json={
                            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        })
                        if r.status_code == 429:
                            time.sleep(r.json().get("parameters", {}).get("retry_after", 1))
                            continue
                        if r.status_code == 403:  # пользователь заблокировал бота
                            self._unsubscribe(chat_id)
                        break
                    except httpx.HTTPError:
                        log.warning("Telegram: не удалось отправить сообщение (попытка %s)", attempt + 1)
                        time.sleep(2)
