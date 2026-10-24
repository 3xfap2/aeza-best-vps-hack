"""Курс евро к рублю по данным ЦБ РФ — для показа сумм в рублях.

Данные и расчёты остаются в евро (как в источнике); рубли — только отображение.
Курс берётся с cbr.ru и кешируется на 6 часов; если ЦБ недоступен — последний известный
курс, а если его нет — ручной FX_EUR_RUB из окружения.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import date, datetime

import httpx

log = logging.getLogger("radar.fx")

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
TTL = 6 * 3600

_lock = threading.Lock()
_cache: dict | None = None   # {"rate", "date", "source", "fetched"}


def parse_cbr(xml: str) -> tuple[float, date]:
    """Из XML_daily.asp: значение EUR за номинал и дата курса."""
    day = re.search(r'ValCurs Date="(\d{2})\.(\d{2})\.(\d{4})"', xml)
    eur = re.search(r"<CharCode>EUR</CharCode>\s*<Nominal>(\d+)</Nominal>.*?<Value>([\d,]+)</Value>", xml, re.S)
    if not day or not eur:
        raise ValueError("В ответе ЦБ нет курса евро")
    rate = float(eur.group(2).replace(",", ".")) / int(eur.group(1))
    return rate, date(int(day.group(3)), int(day.group(2)), int(day.group(1)))


def eur_rub(transport: httpx.BaseTransport | None = None) -> dict | None:
    """{"rate": 90.24, "date": "2026-10-24", "source": "ЦБ РФ" | "вручную", "stale": bool} или None."""
    global _cache
    with _lock:
        if _cache and time.time() - _cache["fetched"] < TTL:
            return _public(_cache, stale=False)
        try:
            with httpx.Client(timeout=5, transport=transport) as http:
                r = http.get(CBR_URL)
                r.raise_for_status()
                rate, day = parse_cbr(r.content.decode("cp1251"))
            _cache = {"rate": round(rate, 4), "date": day.isoformat(), "source": "ЦБ РФ", "fetched": time.time()}
            return _public(_cache, stale=False)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Курс ЦБ недоступен: %s", e)
            if _cache:
                return _public(_cache, stale=True)
    manual = os.environ.get("FX_EUR_RUB")
    if manual:
        return {"rate": float(manual), "date": datetime.now().date().isoformat(), "source": "вручную (FX_EUR_RUB)", "stale": False}
    return None


def cached_rate() -> float | None:
    """Без сетевого запроса — для бота и фоновых задач."""
    return _cache["rate"] if _cache else (float(os.environ["FX_EUR_RUB"]) if os.environ.get("FX_EUR_RUB") else None)


def _public(c: dict, stale: bool) -> dict:
    return {"rate": c["rate"], "date": c["date"], "source": c["source"], "stale": stale}
