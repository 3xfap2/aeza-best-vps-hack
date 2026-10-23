"""Пароли, сессии и проверка доступа.

Сессия — случайный токен; в базе хранится только его SHA-256. Браузер получает
токен в httpOnly-cookie, внешние клиенты API — в ответе login и передают его
в заголовке Authorization: Bearer.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app import db

COOKIE_NAME = "radar_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_hex, digest_hex = stored.split("$")
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.cookies.get(COOKIE_NAME)


@dataclass(frozen=True)
class User:
    id: int
    login: str
    display_name: str
    via_sso: bool


def current_user(request: Request) -> User:
    """Зависимость FastAPI: пускает только с действующей сессией."""
    token = request_token(request)
    if token:
        with request.app.state.pool.connection() as conn:
            row = db.session_user(conn, token_hash(token))
        if row:
            return User(row["id"], row["login"], row["display_name"], row["oidc_subject"] is not None)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужно войти", headers={"WWW-Authenticate": "Bearer"})


class LoginLimiter:
    """Не больше max_attempts неудачных входов за window секунд на пару логин + адрес."""

    def __init__(self, max_attempts: int = 5, window: float = 60) -> None:
        self.max_attempts = max_attempts
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        with self._lock:
            q = self._prune(key)
            if len(q) < self.max_attempts:
                return 0
            return int(self.window - (time.monotonic() - q[0])) + 1

    def fail(self, key: str) -> None:
        with self._lock:
            self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _prune(self, key: str) -> deque[float]:
        q = self._failures[key]
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return q
