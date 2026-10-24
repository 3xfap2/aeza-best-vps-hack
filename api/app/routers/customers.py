from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app import ai, db
from app.insights import message, what_if
from app.security import User, current_user, require
from app.views import CustomerDetail, CustomerPage, HistoryPoint, Level, customer_detail, list_item

router = APIRouter(prefix="/api/v1/customers", tags=["Клиенты"], dependencies=[Depends(current_user)])

SortField = Literal["id", "email", "balance", "totalReplenished", "score", "expectedLoss"]
SORT_KEYS = {
    "id": lambda c: c.account.id,
    "email": lambda c: c.account.email.lower(),
    "balance": lambda c: c.account.balance,
    "totalReplenished": lambda c: c.account.totalReplenished,
    "score": lambda c: c.score,
    "expectedLoss": lambda c: c.expected_loss,
}


def _filtered(request: Request, sort: str, order: str, level, q, hasIssues, cause=None):
    cur = request.app.state.radar.current
    items = cur.result.customers
    if level:
        items = [c for c in items if c.level in level]
    if q:
        needle = q.strip().lower()
        items = [c for c in items if needle in c.account.email.lower() or needle == str(c.account.id)]
    if hasIssues:
        items = [c for c in items if c.quality_issues]
    if cause:
        items = [c for c in items if c.level in ("high", "medium") and c.main_cause == cause]
    key = SORT_KEYS[sort]
    # Клиенты без значения (нет оценки) всегда в конце — при любом направлении.
    present = [c for c in items if key(c) is not None]
    missing = sorted((c for c in items if key(c) is None), key=lambda c: c.account.id)
    present.sort(key=lambda c: (key(c), c.account.id), reverse=order == "desc")
    return cur, present + missing


@router.get("/export.csv", summary="Выгрузка клиентов в CSV (те же фильтры и сортировка, что у списка)",
            response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def export_csv(
    request: Request,
    sort: SortField = Query("score"),
    order: Literal["asc", "desc"] = Query("desc"),
    level: list[Level] | None = Query(None),
    q: str | None = Query(None, max_length=100),
    hasIssues: bool | None = Query(None),
    cause: str | None = Query(None, max_length=64),
    includeEmail: bool = Query(False, description="Добавить email (персональные данные) — по умолчанию выгрузка только с ID"),
) -> Response:
    cur, rows = _filtered(request, sort, order, level, q, hasIssues, cause)
    with request.app.state.pool.connection() as conn:
        last = db.last_actions(conn)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id"] + (["email"] if includeEmail else []) +
               ["баланс_eur", "пополнено_eur", "score", "уровень", "потеря_30д_eur", "причина", "что_сделать", "работа"])
    for c in rows:
        item = list_item(c)
        w.writerow([item.id] + ([item.email] if includeEmail else []) +
                   [item.balance, item.totalReplenished, item.score if item.score is not None else "",
                    item.levelTitle, item.expectedLoss, item.topReason or "", (c.actions or [""])[0],
                    last.get(item.id, {}).get("kind", "")])
    name = f"radar-clients-{cur.result.as_of:%Y%m%d}.csv"
    # BOM — чтобы Excel открыл кириллицу без выбора кодировки
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("", response_model=CustomerPage, summary="Список клиентов с оценками риска; фильтрация и сортировка рейтинга")
def list_customers(
    request: Request,
    sort: SortField = Query("score", description="Поле сортировки"),
    order: Literal["asc", "desc"] = Query("desc", description="Направление"),
    level: list[Level] | None = Query(None, description="Фильтр по уровням, можно несколько"),
    q: str | None = Query(None, max_length=100, description="Поиск по email или ID"),
    hasIssues: bool | None = Query(None, description="Только клиенты с противоречиями в данных"),
    cause: str | None = Query(None, max_length=64, description="Главная причина риска (ключ признака) — клиенты высокого и среднего риска"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=200),
) -> CustomerPage:
    cur, ordered = _filtered(request, sort, order, level, q, hasIssues, cause)
    start = (page - 1) * pageSize
    with request.app.state.pool.connection() as conn:
        last = db.last_actions(conn)
    items = [list_item(c) for c in ordered[start:start + pageSize]]
    for item in items:
        item.lastAction = last.get(item.id, {}).get("kind")
    return CustomerPage(
        items=items,
        total=len(ordered), page=page, pageSize=pageSize, sort=sort, order=order,
        asOf=cur.result.as_of, version=cur.version,
    )


@router.get("/{customer_id}", response_model=CustomerDetail,
            summary="Подробная оценка клиента, его услуг, признаков риска и объяснений",
            responses={404: {"description": "Клиент не найден"}})
def get_customer(customer_id: int, request: Request) -> CustomerDetail:
    cur = request.app.state.radar.current
    c = cur.result.by_id.get(customer_id)
    if c is None:
        raise HTTPException(404, f"Клиент #{customer_id} не найден")
    detail = customer_detail(c, cur.result, cur.snapshot, cur.version)
    with request.app.state.pool.connection() as conn:
        detail.history = [HistoryPoint(score=r["score"], level=r["level"], asOf=r["as_of"], reason=r["reason"],
                                       recordedAt=r["recorded_at"]) for r in db.score_history(conn, customer_id, 30)]
    return detail


class WhatIfOut(BaseModel):
    key: str
    title: str
    score: int | None
    level: str
    expectedLoss: float
    saved: float


@router.get("/{customer_id}/what-if", response_model=list[WhatIfOut],
            summary="Что будет, если клиент пополнит счёт, включит автопродление, оплатит или включит сервер",
            description="Пересчёт той же моделью на копии данных клиента; исходные данные не меняются.")
def customer_what_if(customer_id: int, request: Request) -> list[WhatIfOut]:
    cur = request.app.state.radar.current
    if customer_id not in cur.result.by_id:
        raise HTTPException(404, f"Клиент #{customer_id} не найден")
    return [WhatIfOut(**x) for x in what_if(cur.result, cur.snapshot, customer_id)]


ActionKind = Literal["called", "no_answer", "messaged", "offer", "renewed", "churned"]
CHURN_REASONS = ("Дорого", "Ушёл к конкуренту", "Проект закрыт", "Технические проблемы", "Не пользуется", "Другое")


class ActionIn(BaseModel):
    kind: ActionKind
    reason: Literal[CHURN_REASONS] | None = None  # type: ignore[valid-type]
    note: str | None = Field(None, max_length=500)
    followUpDays: int | None = Field(None, ge=1, le=60, description="Перезвонить через N дней (от даты расчёта); "
                                                                     "в этот день клиент вернётся в «Сегодня»")


class ActionOut(BaseModel):
    id: int
    kind: str
    reason: str | None
    note: str | None
    author: str
    createdAt: datetime
    followUp: date | None = None


@router.get("/{customer_id}/actions", response_model=list[ActionOut], summary="Журнал работы с клиентом")
def list_actions(customer_id: int, request: Request) -> list[ActionOut]:
    with request.app.state.pool.connection() as conn:
        return [ActionOut(**r) for r in db.actions_for(conn, customer_id)]


@router.post("/{customer_id}/actions", response_model=ActionOut, status_code=201,
             summary="Отметить работу с клиентом: позвонили, написали, предложение, продлил, ушёл (с причиной)")
def add_action(customer_id: int, body: ActionIn, request: Request, user: User = Depends(require("marketer"))) -> ActionOut:
    customer = request.app.state.radar.current.result.by_id.get(customer_id)
    if customer is None:
        raise HTTPException(404, f"Клиент #{customer_id} не найден")
    if body.kind == "churned" and not body.reason:
        raise HTTPException(422, "Для «ушёл» укажите причину")
    as_of = request.app.state.radar.current.result.as_of
    follow_up = as_of.date() + timedelta(days=body.followUpDays) if body.followUpDays and body.kind not in ("renewed", "churned") else None
    with request.app.state.pool.connection() as conn:
        row = db.add_action(conn, customer_id, body.kind, body.reason if body.kind == "churned" else None, body.note, user.login,
                            round(customer.expected_loss), follow_up, customer.score)
    request.app.state.radar.broker.publish("action", {"customerId": customer_id, "kind": body.kind})
    return ActionOut(**row)


class MessageOut(BaseModel):
    subject: str
    text: str
    cause: str
    email: str
    generatedBy: Literal["template", "ai"] = Field(description="template — шаблон причины; ai — персональный черновик ИИ "
                                                           "(все числа сверены с данными); отправляет маркетолог")
    note: str | None = Field(None, description="Почему показан шаблон, хотя просили ИИ")


@router.get("/{customer_id}/message", response_model=MessageOut | None,
            summary="Черновик сообщения клиенту по главной причине риска")
def customer_message(customer_id: int, request: Request,
                     useAi: bool = Query(False, description="Персональный черновик ИИ; при сбое или непроверенных числах — шаблон")) -> MessageOut | None:
    cur = request.app.state.radar.current
    if customer_id not in cur.result.by_id:
        raise HTTPException(404, f"Клиент #{customer_id} не найден")
    m = message(cur.result, customer_id)
    if not m or not useAi:
        return MessageOut(**m) if m else None
    client = getattr(request.app.state, "ai", None)
    if client is None:
        return MessageOut(**m, note="ИИ не подключён — показан шаблон")
    emails = {a.email.lower(): a.id for a in cur.snapshot.accounts}
    with request.app.state.pool.connection() as conn:
        facts = ai.customer_facts(cur.result, cur.snapshot, customer_id, db.actions_for(conn, customer_id, 5))
    try:
        draft = ai.message(client, ai.scrub(facts or "", emails), cur.result.by_id[customer_id].contributions[0].title)
    except ai.AIError as e:
        return MessageOut(**m, note=f"Показан шаблон: {e}")
    return MessageOut(**{**m, **draft, "generatedBy": "ai"})
