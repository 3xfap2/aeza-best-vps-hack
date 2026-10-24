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
    oidc_scopes: str
    oidc_role_claim: str
    oidc_role_map: dict[str, str]
    oidc_default_role: str
    public_url: str
    telegram_token: str | None
    telegram_link_code: str | None
    telegram_digest_hour: int | None


def _path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _role_map(value: str) -> dict[str, str]:
    """«radar-admins=admin, viewer@radar.example=viewer» → {группа или email: роль}."""
    out = {}
    for pair in filter(None, (p.strip() for p in value.split(","))):
        key, _, role = pair.partition("=")
        if role.strip() not in ("admin", "marketer", "viewer"):
            raise RuntimeError(f"OIDC_ROLE_MAP: роль «{role.strip()}» — нужна admin, marketer или viewer")
        out[key.strip().lower()] = role.strip()
    return out


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
        oidc_scopes=os.environ.get("OIDC_SCOPES") or "openid email profile",
        oidc_role_claim=os.environ.get("OIDC_ROLE_CLAIM") or "groups",
        oidc_role_map=_role_map(os.environ.get("OIDC_ROLE_MAP", "")),
        oidc_default_role=os.environ.get("OIDC_DEFAULT_ROLE") or "marketer",
        public_url=os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/"),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_link_code=link_code,
        telegram_digest_hour=int(digest) if digest.isdigit() else None,
    )
