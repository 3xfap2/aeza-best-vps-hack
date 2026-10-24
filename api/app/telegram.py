"""Telegram-бот маркетолога.

Подписка: /start <TELEGRAM_LINK_CODE>. Без кода бот ничего не показывает — в нём данные клиентов.
Уведомления: после каждого пересчёта — о клиентах, которые перешли в высокий риск;
каждое утро (TELEGRAM_DIGEST_HOUR по Москве) — план на день из очереди удержания.
Команды: /today, /summary, /top, /client <id или email>, /stop.
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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx

from app import db
from app.retention import queue as retention_queue
from app.scoring.engine import LEVEL_TITLES, CustomerResult, EngineResult
from app.views import eur

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger("radar.telegram")

MAX_ALERTS_PER_RECALC = 5
MAX_TODAY = 8
MSK = timezone(timedelta(hours=3))
# План на день считается с теми же допущениями, что экран «Удержание» по умолчанию.
SAVE_RATE, CALL_COST = 30, 3
HELP = (
    "<b>Команды</b>\n"
    "/today — кому звонить и писать сегодня, сколько можно сохранить\n"
    "/summary — сколько денег под угрозой и клиенты по уровням\n"
    "/top — 5 клиентов с наибольшей ожидаемой потерей\n"
    "/client 42 — карточка клиента: причины риска и что сделать\n"
    "/guide — как пользоваться Радаром\n"
    "/stop — отключить уведомления"
)

# Меню команд в Telegram (кнопка «/» рядом с полем ввода).
COMMANDS = [
    ("today", "Кому звонить сегодня"),
    ("summary", "Сколько денег под угрозой"),
    ("top", "5 клиентов с наибольшей потерей"),
    ("client", "Карточка клиента: /client 42"),
    ("guide", "Как пользоваться Радаром"),
    ("help", "Что умеет бот и как подключиться"),
    ("stop", "Отключить уведомления"),
]

GUIDE = (
    "<b>Как пользоваться Радаром оттока</b>\n\n"
    "1. <b>Сумма под угрозой</b> (/summary) — сколько выручки за 30 дней, скорее всего, не продлят.\n"
    "2. <b>Кому звонить первым</b> (/top) — клиенты с наибольшей потерей. У каждого причина и готовое действие.\n"
    "3. <b>Почему риск</b> (/client 42) — вклад каждого признака: «ждёт оплаты», «не хватает средств», "
    "«автопродление выключено»…\n"
    "4. <b>План на день</b> (/today) — кому звонить и писать сегодня: что удалится раньше всего и окупится ли звонок. "
    "Каждое утро присылаю его сам.\n"
    "5. <b>Уведомления</b> — когда клиент переходит в высокий риск, я сразу пишу сюда.\n"
    "6. <b>Панель</b> — то же и больше: «что будет, если клиент пополнит счёт», календарь продлений, "
    "настройка модели. Ссылка — в каждом уведомлении.\n\n"
    "<b>Уровни риска:</b> высокий — score от 60, средний — от 30, низкий — ниже. "
    "Score 0–100 — сумма баллов по признакам."
)

EXAMPLE = (
    "<b>Пример уведомления</b> (демо):\n"
    "<b>В высокий риск перешли: 1</b>\n\n"
    "<b>user-0062@example.test</b> · score 100 · € 54,19\n"
    "Ждёт оплаты, до удаления 20 ч\n"
    "→ Предложить пополнить счёт на € 90,76 сегодня"
)


def _money(cents: float) -> str:
    return f"€ {eur(cents):,.2f}".replace(",", " ").replace(".", ",")


class TelegramBot:
    def __init__(self, token: str, link_code: str, radar: "AppState", public_url: str,
                 digest_hour: int | None = None) -> None:
        self.api = f"https://api.telegram.org/bot{token}"
        self.link_code = link_code
        self.radar = radar
        self.public_url = public_url
        self.digest_hour = digest_hour
        self._digest_sent: str | None = None
        self._outbox: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.username: str | None = None

    # --- жизненный цикл ------------------------------------------------------

    def start(self) -> None:
        for target, name in ((self._poll, "tg-poll"), (self._send_loop, "tg-send"), (self._digest_loop, "tg-digest")):
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
            lines.append(f"\nи ещё {len(escalated) - MAX_ALERTS_PER_RECALC} — в панели: {self.public_url}/app/customers")
        self._broadcast("\n".join(lines))

    def _digest_loop(self) -> None:
        """Раз в день в digest_hour по Москве — план на день всем подписчикам."""
        while self.digest_hour is not None and not self._stop.wait(60):
            try:
                text = self.digest(datetime.now(MSK))
                if text:
                    self._broadcast(text)
            except Exception:  # база или расчёт недоступны — попробуем через минуту
                log.warning("Telegram: не удалось собрать утренний план", exc_info=True)

    def digest(self, now: datetime) -> str | None:
        """Утренний план, если пришёл час и сегодня ещё не отправляли."""
        today = now.date().isoformat()
        if self.digest_hour is None or now.hour != self.digest_hour or self._digest_sent == today:
            return None
        self._digest_sent = today
        return "<b>Доброе утро! План на сегодня</b>\n" + self._today()

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
        parts.append(f"{self.public_url}/app/customers/{c.account.id}")
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

    def _today(self) -> str:
        """Очередь удержания на сегодня: что удалится раньше всего, звонок или письмо, сколько сохраним."""
        result = self.radar.current.result
        with self.radar.pool.connection() as conn:
            last = db.last_actions(conn)
        items = [x for x in retention_queue(result, last, SAVE_RATE, CALL_COST) if x["status"] != "done"]
        today = [x for x in items if x["bucket"] == "today"]
        title = "сегодня"
        if not today:
            today, title = [x for x in items if x["bucket"] == "week"], "на неделе (на сегодня срочных нет)"
        if not today:
            return "Клиентов, с которыми нужно связаться в ближайшую неделю, нет."
        saved = sum(x["expectedSaved"] for x in today)
        calls = sum(1 for x in today if x["channel"] == "call")
        lines = [f"<b>Связаться {title}: {len(today)}</b> · можно сохранить {_money(saved * 100)}",
                 f"звонков {calls}, писем {len(today) - calls} (удержание {SAVE_RATE} %, звонок € {CALL_COST})"]
        for x in today[:MAX_TODAY]:
            when = {"overdue": "удаление задержано", "deletion": f"удалится {x['deadline']:%d.%m %H:%M} UTC",
                    "renewal": f"продление {x['deadline']:%d.%m}" if x["daysLeft"] > 0 else "срок истёк, продление не прошло"}[x["deadlineKind"]]
            channel = "звонок" if x["channel"] == "call" else "письмо"
            lines += ["", f"<b>{html.escape(x['email'])}</b> · {when}",
                      f"{channel} · сохраним {_money(x['expectedSaved'] * 100)} · {html.escape(x['causeTitle'] or '')}"]
            if x["action"]:
                lines.append(f"→ {html.escape(x['action'])}")
            if x["lastAction"]:
                lines.append(f"уже в работе: {x['lastAuthor']}")
        if len(today) > MAX_TODAY:
            lines.append(f"\nи ещё {len(today) - MAX_TODAY}")
        lines.append(f"\nОчередь и отметки: {self.public_url}/app/retention")
        return "\n".join(lines)

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
        subscribed = self._is_subscribed(chat_id)
        if command == "/start":
            if arg.strip() and hmac.compare_digest(arg.strip(), self.link_code):
                if not subscribed:
                    self._subscribe(chat_id, username)
                return ("<b>Готово, вы подключены.</b> Буду присылать клиентов, перешедших в высокий риск.\n\n"
                        + self._summary() + "\n\n" + HELP)
            if arg.strip():
                return "Код не подошёл. Откройте бота кнопкой «Подключить» в панели — код подставится сам.\n\n" + self._welcome()
            return HELP if subscribed else self._welcome()
        if command == "/guide":
            return GUIDE if subscribed else GUIDE + "\n\n" + self._how_to_connect()
        if command == "/help":
            return HELP if subscribed else self._welcome()
        if not subscribed:
            return "Эта команда работает после подключения.\n\n" + self._how_to_connect()
        if command == "/stop":
            self._unsubscribe(chat_id)
            return "Уведомления отключены. Вернуться: /start <код доступа>."
        if command == "/today":
            return self._today()
        if command == "/summary":
            return self._summary()
        if command == "/top":
            return self._top()
        if command == "/client":
            return self._client(arg) if arg.strip() else "Укажите ID или email: /client 42"
        return HELP

    def _how_to_connect(self) -> str:
        return (
            "<b>Как подключиться</b>\n"
            f"1. Войдите в панель: {self.public_url}/login\n"
            "2. «Данные» → «Интеграции» → «Подключить» — бот откроется с кодом доступа, нажмите «Старт».\n"
            "Код доступа нужен, потому что в уведомлениях — данные клиентов."
        )

    def _welcome(self) -> str:
        return (
            "Привет! Я бот <b>Радара оттока</b> — помогаю маркетологу хостинга не терять клиентов.\n\n"
            "• сообщаю, когда клиент переходит в высокий риск: кто, почему и что сделать;\n"
            "• каждое утро присылаю план: кому звонить и писать сегодня;\n"
            "• по команде показываю сумму под угрозой и кому звонить первым.\n\n"
            + self._how_to_connect() + "\n\n" + EXAMPLE + "\n\n/guide — как пользоваться Радаром"
        )

    # --- сеть ----------------------------------------------------------------

    def _poll(self) -> None:
        offset = 0
        with httpx.Client(timeout=40) as http:
            while not self._stop.is_set():
                try:
                    if self.username is None:  # имя нужно для ссылки «Подключить» в панели
                        self.username = http.get(f"{self.api}/getMe").json()["result"]["username"]
                        http.post(f"{self.api}/setMyCommands", json={
                            "commands": [{"command": c, "description": d} for c, d in COMMANDS]})
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
