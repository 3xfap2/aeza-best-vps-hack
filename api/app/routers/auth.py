from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app import db
from app.security import COOKIE_NAME, User, current_user, new_token, request_token, token_hash, verify_password
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
    return UserOut(id=u.id, login=u.login, displayName=u.display_name, viaSso=u.via_sso)


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
    if not row or not verify_password(body.password, row["password_hash"]):
        limiter.fail(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    limiter.reset(key)
    token, expires = start_session(request, response, row["id"])
    user = User(row["id"], row["login"], row["display_name"], False)
    return LoginOut(user=user_out(user), token=token, expiresAt=expires)


@router.get("/me", response_model=UserOut, summary="Текущий пользователь")
def me(user: User = Depends(current_user)) -> UserOut:
    return user_out(user)


@router.post("/logout", status_code=204, summary="Завершение текущей сессии")
def logout(request: Request, response: Response, user: User = Depends(current_user)) -> Response:
    token = request_token(request)
    if token:
        with request.app.state.pool.connection() as conn:
            db.delete_session(conn, token_hash(token))
    response.status_code = 204
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
