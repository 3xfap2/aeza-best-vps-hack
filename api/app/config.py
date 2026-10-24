from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    database_url: str
    admin_login: str
    admin_password: str
    metrics_token: str
    session_hours: int
    cookie_secure: bool
    seed_path: Path | None
    case_path: Path | None
    demo_path: Path | None
    oidc_issuer: str | None
    oidc_public_issuer: str | None
    oidc_client_id: str | None
    oidc_client_secret: str | None
    public_url: str
    telegram_token: str | None
    telegram_link_code: str | None
    telegram_digest_hour: int | None


def _path(value: str | None) -> Path | None:
    return Path(value) if value else None


def load_config() -> Config:
    password = os.environ.get("ADMIN_PASSWORD", "")
    if len(password) < 8:
        raise RuntimeError("ADMIN_PASSWORD не задан или короче 8 символов")
    link_code = os.environ.get("TELEGRAM_LINK_CODE") or None
    if os.environ.get("TELEGRAM_BOT_TOKEN") and (not link_code or len(link_code) < 8):
        raise RuntimeError("Для бота нужен TELEGRAM_LINK_CODE не короче 8 символов")
    digest = os.environ.get("TELEGRAM_DIGEST_HOUR", "9").strip().lower()
    if digest not in ("", "off") and not (digest.isdigit() and 0 <= int(digest) <= 23):
        raise RuntimeError("TELEGRAM_DIGEST_HOUR — час по Москве 0–23 или off")
    token = os.environ.get("METRICS_TOKEN", "")
    if len(token) < 16:
        raise RuntimeError("METRICS_TOKEN не задан или короче 16 символов")
    return Config(
        database_url=os.environ.get("DATABASE_URL", "postgresql://radar@127.0.0.1:5434/radar"),
        admin_login=os.environ.get("ADMIN_LOGIN", "admin"),
        admin_password=password,
        metrics_token=token,
        session_hours=int(os.environ.get("SESSION_HOURS", "12")),
        cookie_secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        # При первом запуске грузим расширенный набор: данные кейса + сгенерированные клиенты.
        seed_path=_path(os.environ.get("SEED_PATH", str(ROOT.parent / "data" / "demo" / "data.json"))),
        case_path=_path(os.environ.get("CASE_PATH", str(ROOT.parent / "data" / "case" / "data.json"))),
        demo_path=_path(os.environ.get("DEMO_PATH", str(ROOT.parent / "data" / "demo" / "data.json"))),
        oidc_issuer=os.environ.get("OIDC_ISSUER") or None,
        oidc_public_issuer=os.environ.get("OIDC_PUBLIC_ISSUER") or os.environ.get("OIDC_ISSUER") or None,
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID") or None,
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET") or None,
        public_url=os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/"),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_link_code=link_code,
        telegram_digest_hour=int(digest) if digest.isdigit() else None,
    )
