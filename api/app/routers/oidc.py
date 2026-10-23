"""Вход через OpenID Connect (Authorization Code Flow).

Браузер ходит к провайдеру по публичному адресу (OIDC_PUBLIC_ISSUER), а сервер
обменивает код и берёт ключи по внутреннему (OIDC_ISSUER) — в Docker это разные
адреса одного Dex. Подпись id_token, iss, aud, exp и nonce проверяются.
Без настроек OIDC эндпоинты отвечают 404, а вход по паролю работает как обычно.
"""
from __future__ import annotations

import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app import db
from app.routers.auth import start_session

router = APIRouter(prefix="/api/v1/auth/oidc", tags=["Авторизация"])

STATE_TTL = 600
_pending: dict[str, tuple[str, float]] = {}
_pending_lock = threading.Lock()


class OidcConfigOut(BaseModel):
    enabled: bool
    loginUrl: str | None


def _cfg(request: Request):
    cfg = request.app.state.config
    if not (cfg.oidc_issuer and cfg.oidc_client_id and cfg.oidc_client_secret):
        raise HTTPException(404, "Вход через SSO не настроен")
    return cfg


def _redirect_uri(cfg) -> str:
    return f"{cfg.public_url}/api/v1/auth/oidc/callback"


@router.get("/config", response_model=OidcConfigOut, summary="Включён ли вход через SSO (без авторизации)")
def oidc_config(request: Request) -> OidcConfigOut:
    cfg = request.app.state.config
    enabled = bool(cfg.oidc_issuer and cfg.oidc_client_id and cfg.oidc_client_secret)
    return OidcConfigOut(enabled=enabled, loginUrl="/api/v1/auth/oidc/login" if enabled else None)


@router.get("/login", summary="Начать вход через SSO: переадресация к провайдеру")
def oidc_login(request: Request) -> RedirectResponse:
    cfg = _cfg(request)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    now = time.monotonic()
    with _pending_lock:
        for key in [k for k, (_, t) in _pending.items() if now - t > STATE_TTL]:
            del _pending[key]
        _pending[state] = (nonce, now)
    query = urlencode({
        "client_id": cfg.oidc_client_id, "redirect_uri": _redirect_uri(cfg), "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce,
    })
    return RedirectResponse(f"{cfg.oidc_public_issuer}/auth?{query}", status_code=302)


@router.get("/callback", summary="Возврат от провайдера SSO", include_in_schema=False)
def oidc_callback(request: Request, code: str | None = None, state: str | None = None,
                  error: str | None = None) -> Response:
    cfg = _cfg(request)
    if error:
        return RedirectResponse(f"/login?sso_error={error}", status_code=302)
    with _pending_lock:
        pending = _pending.pop(state or "", None)
    if not code or not pending or time.monotonic() - pending[1] > STATE_TTL:
        return RedirectResponse("/login?sso_error=state", status_code=302)
    nonce = pending[0]

    try:
        with httpx.Client(timeout=10) as http:
            token = http.post(
                f"{cfg.oidc_issuer}/token",
                data={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(cfg)},
                auth=(cfg.oidc_client_id, cfg.oidc_client_secret),
            )
            token.raise_for_status()
            jwks = http.get(f"{cfg.oidc_issuer}/keys")
            jwks.raise_for_status()
        claims = JsonWebToken(["RS256", "ES256"]).decode(
            token.json()["id_token"], JsonWebKey.import_key_set(jwks.json()),
            claims_options={
                "iss": {"essential": True, "value": cfg.oidc_public_issuer},
                "aud": {"essential": True, "value": cfg.oidc_client_id},
                "nonce": {"essential": True, "value": nonce},
            },
        )
        claims.validate(leeway=60)
    except (httpx.HTTPError, KeyError, ValueError, JoseError):
        return RedirectResponse("/login?sso_error=token", status_code=302)

    email = claims.get("email") or claims["sub"]
    name = claims.get("name") or email
    with request.app.state.pool.connection() as conn:
        user = db.upsert_oidc_user(conn, claims["sub"], email, name)
    response = RedirectResponse("/", status_code=302)
    start_session(request, response, user["id"])
    return response
