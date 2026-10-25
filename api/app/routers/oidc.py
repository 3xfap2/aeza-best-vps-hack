"""Вход через OpenID Connect (Authorization Code Flow).

Браузер ходит к провайдеру по публичному адресу (OIDC_PUBLIC_ISSUER), а сервер
обменивает код и берёт ключи по внутреннему (OIDC_ISSUER) — в Docker это разные
адреса одного Dex. Подпись id_token, iss, aud, exp и nonce проверяются.
Без настроек OIDC эндпоинты отвечают 404, а вход по паролю работает как обычно.

Роль приходит из системы входа компании: claim OIDC_ROLE_CLAIM (по умолчанию groups,
как в Keycloak и Authentik) сопоставляется с ролями по OIDC_ROLE_MAP; ключом может быть
и email. Если совпадение есть, роль обновляется при каждом входе — источник правды IdP.
Если нет — новому пользователю даётся OIDC_DEFAULT_ROLE, а роль можно сменить в «Команде».
"""
from __future__ import annotations

import base64
import hashlib
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

from app import audit, db
from app.routers.auth import start_session

router = APIRouter(prefix="/api/v1/auth/oidc", tags=["Авторизация"])

STATE_TTL = 600
_pending: dict[str, tuple[str, str, float]] = {}  # state → (nonce, code_verifier, время)
_pending_lock = threading.Lock()


class OidcConfigOut(BaseModel):
    enabled: bool
    loginUrl: str | None


def _cfg(request: Request):
    cfg = request.app.state.config
    if not (cfg.oidc_issuer and cfg.oidc_client_id and cfg.oidc_client_secret):
        raise HTTPException(404, "Вход через SSO не настроен")
    return cfg


ROLE_RANK = ("viewer", "marketer", "admin")


def role_from_claims(claims: dict, claim: str, role_map: dict[str, str]) -> str | None:
    """Самая сильная роль среди групп пользователя и его email; None — совпадений нет."""
    values = claims.get(claim) or []
    if isinstance(values, str):
        values = [values]
    keys = [str(v).lower() for v in values] + [str(claims.get("email") or "").lower()]
    roles = [role_map[k] for k in keys if k in role_map]
    return max(roles, key=ROLE_RANK.index) if roles else None


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
    verifier = secrets.token_urlsafe(48)  # PKCE: код без verifier бесполезен, даже если его перехватят
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    now = time.monotonic()
    with _pending_lock:
        for key in [k for k, (_, _, t) in _pending.items() if now - t > STATE_TTL]:
            del _pending[key]
        _pending[state] = (nonce, verifier, now)
    query = urlencode({
        "client_id": cfg.oidc_client_id, "redirect_uri": _redirect_uri(cfg), "response_type": "code",
        "scope": cfg.oidc_scopes, "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{cfg.oidc_public_issuer}/auth?{query}", status_code=302)


@router.get("/callback", summary="Возврат от провайдера SSO", include_in_schema=False)
def oidc_callback(request: Request, code: str | None = None, state: str | None = None,
                  error: str | None = None) -> Response:
    cfg = _cfg(request)
    if error:
        return RedirectResponse(f"/login?{urlencode({'sso_error': error})}", status_code=302)
    with _pending_lock:
        pending = _pending.pop(state or "", None)
    if not code or not pending or time.monotonic() - pending[2] > STATE_TTL:
        return RedirectResponse("/login?sso_error=state", status_code=302)
    nonce, verifier = pending[0], pending[1]

    try:
        with httpx.Client(timeout=10) as http:
            token = http.post(
                f"{cfg.oidc_issuer}/token",
                data={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(cfg),
                      "code_verifier": verifier},
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
    role = role_from_claims(dict(claims), cfg.oidc_role_claim, cfg.oidc_role_map)
    with request.app.state.pool.connection() as conn:
        user = db.upsert_oidc_user(conn, claims["sub"], email, name, role or cfg.oidc_default_role, sync_role=role is not None)
        audit.record(conn, request, "login", user["login"], details={"method": "sso"})
    response = RedirectResponse("/app", status_code=302)
    start_session(request, response, user["id"])
    return response
