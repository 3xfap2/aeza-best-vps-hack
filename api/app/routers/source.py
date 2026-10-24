from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Union

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from app import db, simulator
from app.scoring.settings import AsOf, diff_settings
from app.security import User, current_user, require
from app.source import SnapshotIn

router = APIRouter(prefix="/api/v1/source", tags=["Данные источника"])


class LoadResult(BaseModel):
    accounts: int
    services: int
    transactions: int
    statusChanges: int = Field(description="Сколько услуг сменили статус относительно прошлого снимка")
    version: int


class TopupEvent(BaseModel):
    type: Literal["topup"]
    accountId: int
    amount: float = Field(gt=0, le=100_000, description="Сумма пополнения, €")


class AutoprolongEvent(BaseModel):
    type: Literal["autoprolong"]
    serviceId: int
    enabled: bool


class StatusEvent(BaseModel):
    type: Literal["service_status"]
    serviceId: int
    status: Literal["active", "suspended", "prolong_wait", "deleted", "blocked"]


class PaymentEvent(BaseModel):
    type: Literal["pay_renewal"]
    serviceId: int


class SignalEvent(BaseModel):
    type: Literal["signal"]
    name: Literal["supportTickets30d", "loadDrop", "downgraded"] = Field(
        description="supportTickets30d — для аккаунта (targetId = id аккаунта); loadDrop, downgraded — для услуги")
    targetId: int
    value: float | bool


SourceEvent = Annotated[Union[TopupEvent, AutoprolongEvent, StatusEvent, PaymentEvent, SignalEvent],
                        Field(discriminator="type")]


class EventResult(BaseModel):
    summary: str
    version: int


class SimulateIn(BaseModel):
    days: float = Field(gt=0, le=60, description="На сколько дней сдвинуть дату расчёта")


class SimulateResult(BaseModel):
    asOf: datetime
    notes: list[str]
    version: int


class SnapshotInfo(BaseModel):
    id: int
    referenceAt: datetime | None
    counts: dict[str, int]
    loadedBy: str
    loadedAt: datetime


class ActivityItem(BaseModel):
    id: int
    kind: str
    summary: str
    author: str
    createdAt: datetime


def load_snapshot(request: Request, snap: SnapshotIn, author: str, kind: str) -> LoadResult:
    radar = request.app.state.radar
    with radar.write_lock:
        with request.app.state.pool.connection() as conn:
            counts = db.replace_snapshot(conn, snap, author, datetime.now(timezone.utc))
            db.log_activity(
                conn, kind,
                f"Загружен снимок: {counts['accounts']} аккаунтов, {counts['services']} услуг, "
                f"{counts['transactions']} транзакций; смен статуса: {counts['statusChanges']}",
                author, counts,
            )
        cur = radar.recalculate(kind)
    return LoadResult(**counts, version=cur.version)


@router.put("/snapshot", response_model=LoadResult,
            summary="Загрузка или полная замена снимка исходных данных с автоматическим пересчётом",
            description="Формат — как data.json из комплекта кейса: {accounts, services, transactions}, "
                        "суммы в евроцентах. Необязательное поле referenceAt — момент снимка.",
            responses={422: {"description": "Данные не прошли проверку; в detail указано поле"}})
def put_snapshot(request: Request, snap: SnapshotIn, user: User = Depends(require("admin"))) -> LoadResult:
    return load_snapshot(request, snap, user.login, "snapshot")


@router.get("/snapshot", response_model=SnapshotInfo | None, summary="Сведения о последнем загруженном снимке")
def snapshot_info(request: Request, _: User = Depends(current_user)) -> SnapshotInfo | None:
    with request.app.state.pool.connection() as conn:
        info = db.snapshot_info(conn)
    return SnapshotInfo(**info) if info else None


@router.post("/preset/{name}", response_model=LoadResult,
             summary="Загрузить готовый набор: case — данные кейса (10 клиентов), extended — кейс + 190 сгенерированных")
def load_preset(name: Literal["case", "extended"], request: Request, user: User = Depends(require("admin"))) -> LoadResult:
    cfg = request.app.state.config
    path = cfg.case_path if name == "case" else cfg.demo_path
    if not path or not path.exists():
        raise HTTPException(404, "Файл набора не найден")
    try:
        snap = SnapshotIn.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValidationError as e:
        raise HTTPException(500, f"Набор повреждён: {e.errors()[0]['msg']}") from e
    return load_snapshot(request, snap, user.login, f"preset:{name}")


@router.post("/events", response_model=EventResult,
             summary="Событие из системы-источника (пополнение, оплата, автопродление, смена статуса)",
             responses={404: {"description": "Аккаунт или услуга не найдены"}, 409: {"description": "Событие невозможно"}})
def post_event(request: Request, event: SourceEvent = Body(...), user: User = Depends(require("admin"))) -> EventResult:
    radar = request.app.state.radar
    with radar.write_lock:
        at = radar.current.result.as_of
        try:
            with request.app.state.pool.connection() as conn:
                summary = _apply_event(conn, event, at)
                db.log_activity(conn, "event", summary, user.login, event.model_dump())
        except simulator.SimulationError as e:
            code = 404 if "не найден" in str(e) else 409
            raise HTTPException(code, str(e)) from e
        cur = radar.recalculate("event")
    return EventResult(summary=summary, version=cur.version)


def _apply_event(conn, event: Any, at: datetime) -> str:
    if isinstance(event, TopupEvent):
        return simulator.topup(conn, event.accountId, round(event.amount * 100))
    if isinstance(event, AutoprolongEvent):
        return simulator.set_autoprolong(conn, event.serviceId, event.enabled)
    if isinstance(event, StatusEvent):
        return simulator.set_status(conn, event.serviceId, event.status, at)
    if isinstance(event, SignalEvent):
        return simulator.set_signal(conn, event.targetId, event.name, event.value)
    return simulator.pay_renewal(conn, event.serviceId, at)


@router.post("/simulate", response_model=SimulateResult,
             summary="Перемотка времени: сдвинуть дату расчёта и применить правила жизненного цикла услуг")
def simulate(request: Request, body: SimulateIn, user: User = Depends(require("admin"))) -> SimulateResult:
    radar = request.app.state.radar
    with radar.write_lock:
        since = radar.current.result.as_of
        target = since + timedelta(days=body.days)
        settings, _ = radar.settings()
        new_settings = settings.model_copy(update={"asOf": AsOf(mode="fixed", value=target)})
        with request.app.state.pool.connection() as conn:
            notes = simulator.advance(conn, since, target)
            db.save_settings(conn, new_settings, diff_settings(settings, new_settings), user.login)
            db.log_activity(conn, "simulate", f"Перемотка на {body.days:g} дн. → {target:%d.%m.%Y %H:%M}; "
                            f"изменений: {len(notes)}", user.login, {"notes": notes})
        cur = radar.recalculate("simulate")
    return SimulateResult(asOf=cur.result.as_of, notes=notes, version=cur.version)


@router.post("/reset", response_model=LoadResult,
             summary="Вернуть данные к последнему загруженному снимку и дату расчёта — к дате снимка")
def reset(request: Request, user: User = Depends(require("admin"))) -> LoadResult:
    radar = request.app.state.radar
    with radar.write_lock:
        with request.app.state.pool.connection() as conn:
            raw = db.last_snapshot_raw(conn)
            if raw is None:
                raise HTTPException(404, "Снимок ещё не загружался")
            snap = SnapshotIn.model_validate(raw)
            counts = db.replace_snapshot(conn, snap, user.login, datetime.now(timezone.utc), record=False)
            db.delete_simulated_transitions(conn)
            settings, _ = radar.settings()
            if settings.asOf.mode != "snapshot":
                new_settings = settings.model_copy(update={"asOf": AsOf()})
                db.save_settings(conn, new_settings, diff_settings(settings, new_settings), user.login)
            db.log_activity(conn, "reset", "Данные возвращены к последнему снимку", user.login)
        cur = radar.recalculate("reset")
    return LoadResult(**counts, version=cur.version)


@router.get("/activity", response_model=list[ActivityItem], summary="Лента последних изменений данных и настроек")
def activity(request: Request, limit: int = Query(50, ge=1, le=500), _: User = Depends(current_user)) -> list[ActivityItem]:
    with request.app.state.pool.connection() as conn:
        return [ActivityItem(**r) for r in db.recent_activity(conn, limit)]
