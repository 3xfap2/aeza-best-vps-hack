from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.security import current_user
from app.views import CustomerDetail, CustomerPage, Level, customer_detail, list_item

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


def _filtered(request: Request, sort: str, order: str, level, q, hasIssues):
    cur = request.app.state.radar.current
    items = cur.result.customers
    if level:
        items = [c for c in items if c.level in level]
    if q:
        needle = q.strip().lower()
        items = [c for c in items if needle in c.account.email.lower() or needle == str(c.account.id)]
    if hasIssues:
        items = [c for c in items if c.quality_issues]
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
) -> Response:
    cur, rows = _filtered(request, sort, order, level, q, hasIssues)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "email", "баланс_eur", "пополнено_eur", "score", "уровень", "потеря_30д_eur", "причина", "что_сделать"])
    for c in rows:
        item = list_item(c)
        w.writerow([item.id, item.email, item.balance, item.totalReplenished, item.score if item.score is not None else "",
                    item.levelTitle, item.expectedLoss, item.topReason or "", (c.actions or [""])[0]])
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
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=200),
) -> CustomerPage:
    cur, ordered = _filtered(request, sort, order, level, q, hasIssues)
    start = (page - 1) * pageSize
    return CustomerPage(
        items=[list_item(c) for c in ordered[start:start + pageSize]],
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
    return customer_detail(c, cur.result, cur.snapshot, cur.version)
