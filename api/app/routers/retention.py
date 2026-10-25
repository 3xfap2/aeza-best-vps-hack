from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from app import audit, db
from app.http import csv_cell
from app.money import to_euros
from app.retention import BUCKETS, compare_strategies, effect, queue
from app.security import User, current_user

STRATEGY_CALLS = 10   # если ёмкость не задана — сравниваем на 10 звонках в день

router = APIRouter(prefix="/api/v1/retention", tags=["Удержание"], dependencies=[Depends(current_user)])

SaveRate = Query(30, ge=0, le=100, description="Какую долю клиентов под риском удаётся удержать контактом, %")
CallCost = Query(3, ge=0, le=1000, description="Стоимость одного звонка (время менеджера), €; письмо считается бесплатным")
CallsPerDay = Query(0, ge=0, le=1000, description="Сколько звонков менеджер успевает за день; 0 — без ограничения")


class QueueItem(BaseModel):
    customerId: int
    email: str
    level: str
    score: int | None
    cause: str | None
    causeTitle: str | None
    kind: Literal["involuntary", "voluntary"]
    action: str | None = Field(description="Что сделать — первая рекомендация модели")
    expectedLoss: float = Field(description="Ожидаемая потеря по прайсу, €")
    expectedLossPaid: float = Field(description="Ожидаемая потеря в пределах платёжеспособности, €")
    deadline: datetime = Field(description="Когда деньги будут потеряны: удаление или продление")
    deadlineKind: Literal["deletion", "overdue", "renewal"]
    daysLeft: float
    bucket: Literal["today", "week", "later"] = Field(description="today — до суток или удаление задержано; week — до 7 дней")
    channel: Literal["call", "email"] = Field(description="call — ожидаемо сохраним больше стоимости звонка")
    expectedSaved: float = Field(description="Реально собираемая потеря × доля удержания, €")
    net: float = Field(description="Выгода контакта: ожидаемо сохраним − стоимость звонка, €")
    lastAction: str | None
    lastActionAt: datetime | None
    lastAuthor: str | None
    status: Literal["new", "in_progress", "done"]
    followUp: date | None = Field(description="Договорились перезвонить в эту дату")
    followUpDue: bool = Field(description="Дата «перезвонить» наступила — клиент в «Сегодня»")
    planned: bool = Field(description="Входит в план звонков на день (первые callsPerDay звонков очереди)")
    contactAt: datetime | None = Field(description="Первая отметка работы с клиентом")
    riskAtContact: float | None = Field(description="Сумма под угрозой в момент первой отметки, €")
    scoreAtContact: int | None


class QueueSummary(BaseModel):
    customers: int
    buckets: dict[str, int]
    expectedLoss: float
    expectedLossPaid: float
    expectedSaved: float = Field(description="Ожидаемо сохраним, если отработать всю очередь, €")
    calls: int
    emails: int
    callsCost: float
    net: float = Field(description="Ожидаемо сохраним − стоимость звонков, €")
    worked: int = Field(description="Клиентов очереди с отметкой работы")
    renewed: int
    churned: int
    savedFact: float = Field(description="Сохранено фактически: сумма под угрозой у клиентов с отметкой «продлил», €")
    followUpsDue: int = Field(description="Перезвонить сегодня")
    planned: int = Field(description="Звонков в плане на день")
    plannedSaved: float = Field(description="Ожидаемо сохраним звонками из плана дня, €")
    reducedAfterContact: float = Field(description="На сколько снизилась сумма под угрозой у отработанных клиентов "
                                                   "(по данным биллинга; совпадение по времени, не доказательство эффекта), €")
    improvedAfterContact: int


class EffectItem(BaseModel):
    customerId: int
    email: str
    contactAt: datetime
    contactBy: str
    riskBefore: float = Field(description="Сумма под угрозой в момент первой отметки, €")
    riskNow: float
    scoreBefore: int | None
    scoreNow: int | None
    levelNow: str
    reduced: float = Field(description="max(0, было − стало), €")


class Strategy(BaseModel):
    key: Literal["plan", "loss", "score"]
    title: str
    saved: float = Field(description="Ожидаемо сохраним за неделю при том же числе звонков в день, €")
    missed: float = Field(description="Упустим: срок потери прошёл раньше, чем дошла очередь звонка, €")
    missedCustomers: int


class QueueOut(BaseModel):
    asOf: datetime
    horizonDays: int
    saveRate: float
    callCost: float
    summary: QueueSummary
    strategies: list[Strategy] = Field(description="Сравнение порядка звонков на неделю при том же ресурсе: план Радара, "
                                                    "по сумме потери, по риску. Сценарий на текущих данных")
    strategyCallsPerDay: int
    items: list[QueueItem]


def _queue(request: Request, save_rate: float, call_cost: float, calls_per_day: int = 0):
    cur = request.app.state.radar.current
    with request.app.state.pool.connection() as conn:
        last = db.last_actions(conn)
        first = db.first_contacts(conn)
        saved_fact = db.saved_by_renewals(conn)
    return cur, last, saved_fact, queue(cur.result, last, save_rate, call_cost, calls_per_day, first), first


@router.get("/queue", response_model=QueueOut,
            summary="Очередь удержания: кому написать или позвонить, до какого срока и окупится ли звонок")
def retention_queue(request: Request, saveRate: float = SaveRate, callCost: float = CallCost,
                    callsPerDay: int = CallsPerDay) -> QueueOut:
    cur, last, saved_fact, items, first = _queue(request, saveRate, callCost, callsPerDay)
    calls = [x for x in items if x["channel"] == "call"]
    planned = [x for x in items if x["planned"]]
    after = effect(cur.result, first)
    return QueueOut(
        asOf=cur.result.as_of, horizonDays=cur.result.settings.horizonDays, saveRate=saveRate, callCost=callCost,
        summary=QueueSummary(
            customers=len(items),
            buckets={b: sum(1 for x in items if x["bucket"] == b) for b in BUCKETS},
            expectedLoss=round(sum(x["expectedLoss"] for x in items), 2),
            expectedLossPaid=round(sum(x["expectedLossPaid"] for x in items), 2),
            expectedSaved=round(sum(x["expectedSaved"] for x in items), 2),
            calls=len(calls), emails=len(items) - len(calls), callsCost=round(len(calls) * callCost, 2),
            net=round(sum(x["net"] for x in items), 2),
            worked=sum(1 for x in items if x["status"] != "new"),
            renewed=sum(1 for r in last.values() if r["kind"] == "renewed"),
            churned=sum(1 for r in last.values() if r["kind"] == "churned"),
            savedFact=to_euros(saved_fact),
            followUpsDue=sum(1 for x in items if x["followUpDue"]),
            planned=len(planned), plannedSaved=round(sum(x["expectedSaved"] for x in planned), 2),
            reducedAfterContact=round(sum(x["reduced"] for x in after), 2),
            improvedAfterContact=sum(1 for x in after if x["reduced"] > 0),
        ),
        strategies=[Strategy(**x) for x in compare_strategies(items, callsPerDay or STRATEGY_CALLS)],
        strategyCallsPerDay=callsPerDay or STRATEGY_CALLS,
        items=[QueueItem(**x) for x in items],
    )


@router.get("/effect", response_model=list[EffectItem],
            summary="Что изменилось после контакта: сумма под угрозой и score в момент первой отметки и сейчас",
            description="Совпадение по времени с данными биллинга, а не доказательство, что сработал именно контакт.")
def retention_effect(request: Request) -> list[EffectItem]:
    cur = request.app.state.radar.current
    with request.app.state.pool.connection() as conn:
        first = db.first_contacts(conn)
    return [EffectItem(**x) for x in effect(cur.result, first)]


BUCKET_TITLE = {"today": "сегодня", "week": "на неделе", "later": "позже"}
DEADLINE_TITLE = {"deletion": "удаление", "overdue": "удаление задержано", "renewal": "продление"}
CHANNEL_TITLE = {"call": "звонок", "email": "письмо"}


@router.get("/queue.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}},
            summary="Очередь удержания в CSV для обзвона и рассылки")
def retention_csv(request: Request, saveRate: float = SaveRate, callCost: float = CallCost,
                  includeEmail: bool = Query(False, description="Добавить email (персональные данные)"),
                  user: User = Depends(current_user)) -> Response:
    cur, _, _, items, _ = _queue(request, saveRate, callCost)
    if includeEmail:   # персональные данные ушли из системы — фиксируем кто и сколько
        with request.app.state.pool.connection() as conn:
            audit.record(conn, request, "export.emails", user.login, "retention/queue.csv", {"customers": len(items)})
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["когда", "срок", "вид_срока", "id"] + (["email"] if includeEmail else []) +
               ["канал", "причина", "что_сделать", "потеря_eur", "реально_eur", "сохраним_eur", "статус"])
    for x in items:
        w.writerow([csv_cell(v) for v in
                    [BUCKET_TITLE[x["bucket"]], f"{x['deadline']:%d.%m.%Y %H:%M}", DEADLINE_TITLE[x["deadlineKind"]], x["customerId"]] +
                    ([x["email"]] if includeEmail else []) +
                    [CHANNEL_TITLE[x["channel"]], x["causeTitle"] or "", x["action"] or "", x["expectedLoss"],
                     x["expectedLossPaid"], x["expectedSaved"], x["lastAction"] or ""]])
    name = f"radar-retention-{cur.result.as_of:%Y%m%d}.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
