"""Вход через аккаунт Aéza: пользователь вводит API-ключ из личного кабинета Aéza.

Радар один раз запрашивает профиль (как официальный SDK AezaGroup/aeza-net-sdk:
GET {AEZA_API_URL}/accounts?current=1 с заголовком X-API-Key) и узнаёт, кто это.
Ключ не сохраняется и не пишется в журналы.

Доступ к данным клиентов не выдаётся автоматически: новый аккаунт получает статус
«ждёт подтверждения», роль назначает администратор в «Команде». Сотрудников можно
пускать сразу — по email или домену из AEZA_AUTO_APPROVE (например, @aeza.net).
"""
from __future__ import annotations

import os

import httpx

DEFAULT_API = "https://core.aeza.net/api"


class AezaAuthError(RuntimeError):
    pass


def api_url() -> str:
    return (os.environ.get("AEZA_API_URL") or DEFAULT_API).rstrip("/")


def fetch_profile(api_key: str, transport: httpx.BaseTransport | None = None) -> dict:
    """{"id", "email", "name"} владельца ключа или AezaAuthError с понятной причиной."""
    try:
        with httpx.Client(timeout=10, transport=transport) as http:
            r = http.get(f"{api_url()}/accounts", params={"current": 1, "extra": 1},
                         headers={"X-API-Key": api_key, "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise AezaAuthError(f"API Aéza недоступно: {type(e).__name__}") from e
    try:
        body = r.json()
    except ValueError as e:
        raise AezaAuthError(f"API Aéza ответило {r.status_code} не в формате JSON") from e
    error = body.get("error") if isinstance(body, dict) else None
    if r.status_code in (401, 403) or error:
        raise AezaAuthError("Ключ API Aéza не подошёл" + (f": {error.get('message')}" if isinstance(error, dict) and error.get("message") else ""))
    if r.status_code != 200:
        raise AezaAuthError(f"API Aéza ответило {r.status_code}")
    data = body.get("data", body)
    items = data.get("items") if isinstance(data, dict) else None
    acc = items[0] if items else data if isinstance(data, dict) and data.get("id") is not None else None
    if not acc or acc.get("id") is None:
        raise AezaAuthError("API Aéza не вернуло профиль аккаунта")
    email = str(acc.get("email") or "").strip().lower()
    name = acc.get("name") or acc.get("username") or acc.get("login") or email or f"Аккаунт Aéza {acc['id']}"
    return {"id": str(acc["id"]), "email": email, "name": str(name)}


def auto_role(email: str, rule: str | None = None) -> str:
    """marketer — если email или его домен в AEZA_AUTO_APPROVE, иначе pending (ждёт администратора)."""
    rule = rule if rule is not None else os.environ.get("AEZA_AUTO_APPROVE", "")
    allowed = {x.strip().lower() for x in rule.split(",") if x.strip()}
    domain = "@" + email.split("@")[-1] if "@" in email else ""
    return "marketer" if email and (email in allowed or domain in allowed) else "pending"
