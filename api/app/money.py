"""Деньги. В данных — целые евроценты (как в источнике), в ответах API — евро, в тексте — «€ 8 655,98».

Между «€» и разрядами — неразрывный пробел: сумма не разрывается переносом строки.
"""
from __future__ import annotations

NBSP = " "


def to_euros(cents: float) -> float:
    """Евроценты → евро для ответов API: 865598 → 8655.98."""
    return round(cents / 100, 2)


def fmt_cents(cents: float) -> str:
    """Евроценты → текст: 865598 → «€ 8 655,98», −100 → «−€ 1,00»."""
    whole, frac = divmod(abs(round(cents)), 100)
    sign = "−" if cents < 0 else ""
    return f"{sign}€{NBSP}{whole:,}".replace(",", NBSP) + f",{frac:02d}"


def fmt_euros(euros: float) -> str:
    """Евро → текст: 8655.98 → «€ 8 655,98»."""
    return fmt_cents(round(euros * 100))
