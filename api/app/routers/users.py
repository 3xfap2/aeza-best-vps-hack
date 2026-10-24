"""Команда: пользователи панели, их роли и работа с клиентами за неделю."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import db
from app.security import User, current_user, hash_password, require
from app.views import eur

router = APIRouter(prefix="/api/v1/users", tags=["Команда"])

Role = Literal["admin", "marketer", "viewer"]
MemberRole = Literal["admin", "marketer", "viewer", "pending"]
WORKLOAD_DAYS = 7


class MemberOut(BaseModel):
    id: int
    login: str
    displayName: str
    role: MemberRole = Field(description="pending — вошёл через Aéza и ждёт, пока администратор выдаст роль")
    viaSso: bool
    viaAeza: bool = False
    createdAt: datetime
    actions: int = Field(description=f"Отметок работы с клиентами за {WORKLOAD_DAYS} дней")
    customers: int = Field(description="Скольких клиентов отработал")
    renewed: int = Field(description="Сколько из них продлили")
    saved: float = Field(description="Сохранено: сумма под угрозой у продливших в момент отметки, €")


class MemberIn(BaseModel):
    login: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$", examples=["anna"])
    displayName: str = Field(min_length=1, max_length=100, examples=["Анна, маркетинг"])
    password: str = Field(min_length=10, max_length=200)
    role: Role = "marketer"


class MemberPatch(BaseModel):
    displayName: str | None = Field(None, min_length=1, max_length=100)
    role: Role | None = None
    password: str | None = Field(None, min_length=10, max_length=200)


def _members(conn) -> list[MemberOut]:
    work = db.workload(conn, WORKLOAD_DAYS)
    out = []
    for u in db.list_users(conn):
        w = work.get(u["login"], {})
        out.append(MemberOut(**u, actions=w.get("actions", 0), customers=w.get("customers", 0),
                             renewed=w.get("renewed", 0), saved=eur(w.get("saved", 0))))
    return out


@router.get("", response_model=list[MemberOut], dependencies=[Depends(current_user)],
            summary="Команда: пользователи, роли и работа с клиентами за 7 дней")
def list_members(request: Request) -> list[MemberOut]:
    with request.app.state.pool.connection() as conn:
        return _members(conn)


@router.post("", response_model=MemberOut, status_code=201, summary="Добавить сотрудника (только администратор)",
             responses={409: {"description": "Такой логин уже есть"}})
def add_member(body: MemberIn, request: Request, user: User = Depends(require("admin"))) -> MemberOut:
    with request.app.state.pool.connection() as conn:
        row = db.create_user(conn, body.login, hash_password(body.password), body.displayName, body.role)
        if row is None:
            raise HTTPException(409, f"Логин «{body.login}» уже занят")
        db.log_activity(conn, "team", f"Добавлен сотрудник {body.login} ({body.role})", user.login)
        return next(m for m in _members(conn) if m.id == row["id"])


@router.patch("/{user_id}", response_model=MemberOut, summary="Изменить имя, роль или пароль сотрудника (только администратор)")
def edit_member(user_id: int, body: MemberPatch, request: Request, user: User = Depends(require("admin"))) -> MemberOut:
    with request.app.state.pool.connection() as conn, conn.transaction():
        target = next((u for u in db.list_users(conn) if u["id"] == user_id), None)
        if target is None:
            raise HTTPException(404, "Сотрудник не найден")
        if body.role and body.role != "admin" and target["role"] == "admin" and db.count_admins(conn) <= 1:
            raise HTTPException(409, "Нельзя снять роль с последнего администратора")
        if body.password and target["viaSso"]:
            raise HTTPException(422, "Сотрудник входит через SSO — пароль задаётся в системе входа компании")
        fields = {"displayName": body.displayName, "role": body.role,
                  "passwordHash": hash_password(body.password) if body.password else None}
        db.update_user(conn, user_id, {k: v for k, v in fields.items() if v is not None})
        changed = [k for k in ("displayName", "role", "password") if getattr(body, k)]
        if changed:
            db.log_activity(conn, "team", f"Изменён сотрудник {target['login']}: {', '.join(changed)}", user.login)
        return next(m for m in _members(conn) if m.id == user_id)


@router.delete("/{user_id}", status_code=204, response_class=Response, summary="Удалить сотрудника и завершить его сессии (только администратор)")
def remove_member(user_id: int, request: Request, user: User = Depends(require("admin"))) -> Response:
    if user_id == user.id:
        raise HTTPException(409, "Нельзя удалить самого себя")
    with request.app.state.pool.connection() as conn, conn.transaction():
        target = next((u for u in db.list_users(conn) if u["id"] == user_id), None)
        if target is None:
            raise HTTPException(404, "Сотрудник не найден")
        if target["role"] == "admin" and db.count_admins(conn) <= 1:
            raise HTTPException(409, "Нельзя удалить последнего администратора")
        db.delete_user(conn, user_id)
        db.log_activity(conn, "team", f"Удалён сотрудник {target['login']}", user.login)
    return Response(status_code=204)
