from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app import aeza_auth, audit, db
from app.security import (
    COOKIE_NAME,
    User,
    current_user,
    new_token,
    request_token,
    token_hash,
    verify_password,
)
from app.views import UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["Авторизация"])


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=100, examples=["admin"])
    password: str = Field(min_length=1, max_length=200)


class LoginOut(BaseModel):
    user: UserOut
    token: str = Field(description="Для клиентов API: передавать в Authorization: Bearer. Браузер получает cookie.")
    expiresAt: datetime


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, login=u.login, displayName=u.display_name, viaSso=u.via_sso, role=u.role)


def start_session(request: Request, response: Response, user_id: int) -> tuple[str, datetime]:
    cfg = request.app.state.config
    token, hashed = new_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=cfg.session_hours)
    with request.app.state.pool.connection() as conn:
        db.create_session(conn, hashed, user_id, expires)
    response.set_cookie(
        COOKIE_NAME, token, max_age=cfg.session_hours * 3600, httponly=True,
        secure=cfg.cookie_secure, samesite="lax", path="/",
    )
    return token, expires


@router.post("/login", response_model=LoginOut, summary="Вход по логину и паролю",
             responses={401: {"description": "Неверный логин или пароль"}, 429: {"description": "Слишком много попыток"}})
def login(body: LoginIn, request: Request, response: Response) -> LoginOut:
    limiter = request.app.state.login_limiter
    key = f"{body.login.lower()}|{request.client.host if request.client else '-'}"
    wait = limiter.retry_after(key)
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Слишком много попыток. Попробуйте через {wait} с",
                            headers={"Retry-After": str(wait)})
    with request.app.state.pool.connection() as conn:
        row = db.user_by_login(conn, body.login)
    if not row or not verify_password(body.password, row["password_hash"]) or row["role"] == "pending":
        limiter.fail(key)
        audit.write(request, "login.failed", body.login.lower(), details={"method": "password"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    limiter.reset(key)
    audit.write(request, "login", row["login"], details={"method": "password"})
    token, expires = start_session(request, response, row["id"])
    user = User(row["id"], row["login"], row["display_name"], False, row["role"])
    return LoginOut(user=user_out(user), token=token, expiresAt=expires)


class AezaLoginIn(BaseModel):
    apiKey: str = Field(min_length=8, max_length=300, description="API-ключ из личного кабинета Aéza; Радар его не сохраняет")


class AezaLoginOut(BaseModel):
    status: Literal["ok", "pending"]
    message: str
    user: UserOut | None = None


@router.post("/aeza", response_model=AezaLoginOut, summary="Вход через аккаунт Aéza (по API-ключу личного кабинета)",
             description="Ключ проверяется запросом профиля к API Aéza и не сохраняется. Новый аккаунт ждёт подтверждения "
                         "администратора (status=pending), если его email или домен не указан в AEZA_AUTO_APPROVE.",
             responses={401: {"description": "Ключ не подошёл"}, 429: {"description": "Слишком много попыток"},
                        502: {"description": "API Aéza недоступно"}})
def aeza_login(body: AezaLoginIn, request: Request, response: Response) -> AezaLoginOut:
    limiter = request.app.state.login_limiter
    key = f"aeza|{request.client.host if request.client else '-'}"
    wait = limiter.retry_after(key)
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Слишком много попыток. Попробуйте через {wait} с",
                            headers={"Retry-After": str(wait)})
    try:
        profile = aeza_auth.fetch_profile(body.apiKey.strip())
    except aeza_auth.AezaAuthError as e:
        limiter.fail(key)
        audit.write(request, "login.failed", "aeza", details={"method": "aeza", "reason": str(e)[:200]})
        code = status.HTTP_502_BAD_GATEWAY if "недоступно" in str(e) or "ответило" in str(e) else status.HTTP_401_UNAUTHORIZED
        raise HTTPException(code, str(e)) from e
    limiter.reset(key)
    with request.app.state.pool.connection() as conn:
        row = db.upsert_aeza_user(conn, profile["id"], profile["email"], profile["name"], aeza_auth.auto_role(profile["email"]))
        if row["role"] == "pending":
            db.log_activity(conn, "team", f"Заявка на доступ через Aéza: {row['login']}", "aeza")
    if row["role"] == "pending":
        return AezaLoginOut(status="pending", message=f"{profile['name']}, заявка на доступ отправлена — администратор Радара "
                                                      "выдаст роль в разделе «Команда». После этого войдите снова.")
    audit.write(request, "login", row["login"], details={"method": "aeza"})
    start_session(request, response, row["id"])
    return AezaLoginOut(status="ok", message="Вход выполнен",
                        user=user_out(User(row["id"], row["login"], row["display_name"], False, row["role"])))


@router.get("/me", response_model=UserOut, summary="Текущий пользователь")
def me(user: User = Depends(current_user)) -> UserOut:
    return user_out(user)


@router.post("/logout", status_code=204, summary="Завершение текущей сессии")
def logout(request: Request, response: Response, user: User = Depends(current_user)) -> Response:
    token = request_token(request)
    if token:
        with request.app.state.pool.connection() as conn:
            db.delete_session(conn, token_hash(token))
            audit.record(conn, request, "logout", user.login)
    response.status_code = 204
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
