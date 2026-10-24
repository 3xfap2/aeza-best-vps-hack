from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from app import db, demo_stream, events, simulator
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
    origin: dict = Field(default_factory=dict, description="kind: case | extended | custom; caseAccounts — клиентов из данных кейса, "
                                                          "otherAccounts — остальных (синтетических или из своего снимка)")
    changesSinceLoad: int = Field(0, description="Событий биллинга и перемоток после загрузки — данные уже не совпадают со снимком")
    title: str = Field("", description="Подпись для панели: какие данные сейчас загружены")


class ActivityItem(BaseModel):
    id: int
    kind: str
    summary: str
    author: str
    createdAt: datetime


def snapshot_origin(snap: SnapshotIn, case_path, kind: str) -> dict:
    """Сколько клиентов из данных кейса (тот же id и email), а сколько — синтетических или своих."""
    case: set[tuple[int, str]] = set()
    if case_path and case_path.exists():
        case = {(a["id"], a["email"]) for a in json.loads(case_path.read_text(encoding="utf-8"))["accounts"]}
    from_case = sum(1 for a in snap.accounts if (a.id, a.email) in case)
    return {"kind": kind, "caseAccounts": from_case, "otherAccounts": len(snap.accounts) - from_case}


def origin_title(origin: dict, accounts: int, changes: int) -> str:
    kind, case, other = origin.get("kind"), origin.get("caseAccounts", 0), origin.get("otherAccounts", 0)
    if kind == "extended":
        title = f"кейс ({case} кл.) + {other} синтетических"
    elif kind == "case":
        title = f"данные кейса, {case} кл."
    else:
        title = f"загруженный снимок, {accounts} кл." + (f" (из них {case} — из кейса)" if case else "")
    return title + (f" · изменений после загрузки: {changes}" if changes else "")


def demo_history(config) -> list[dict]:
    """Прошлые снимки к расширенному набору (tools/gen_history.py) — для сверки модели с исходами."""
    path = config.demo_path.with_name("history.json") if config.demo_path else None
    return json.loads(path.read_text(encoding="utf-8"))["snapshots"] if path and path.exists() else []


def sync_history(conn, config, snap: SnapshotIn, kind: str) -> None:
    """История идёт вместе с данными: у набора кейса её нет, у расширенного — демо, снимок биллинга сам становится историей."""
    if kind == "preset:extended":
        db.replace_history(conn, demo_history(config), "demo")
    elif kind == "preset:case":
        db.clear_history(conn)
    elif snap.referenceAt:
        db.add_history(conn, snap.model_dump(mode="json"), "billing")
    else:
        db.clear_demo_history(conn)


def load_snapshot(request: Request, snap: SnapshotIn, author: str, kind: str) -> LoadResult:
    radar = request.app.state.radar
    origin = snapshot_origin(snap, request.app.state.config.case_path,
                             {"preset:case": "case", "preset:extended": "extended"}.get(kind, "custom"))
    with radar.write_lock:
        with request.app.state.pool.connection() as conn:
            counts = db.replace_snapshot(conn, snap, author, datetime.now(timezone.utc), origin=origin)
            sync_history(conn, request.app.state.config, snap, kind)
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
    if not info:
        return None
    return SnapshotInfo(**info, title=origin_title(info["origin"], info["counts"].get("accounts", 0), info["changesSinceLoad"]))


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


class HistoryOut(BaseModel):
    snapshots: int
    first: datetime | None
    last: datetime | None
    demo: bool = Field(description="История сгенерирована для демонстрации (а не пришла из биллинга)")


@router.get("/history", response_model=HistoryOut, summary="Прошлые снимки для сверки модели с исходами")
def history_info(request: Request, _: User = Depends(current_user)) -> HistoryOut:
    with request.app.state.pool.connection() as conn:
        return HistoryOut(**db.history_info(conn))


@router.post("/history", response_model=HistoryOut,
             summary="Добавить прошлый снимок из архива биллинга (для сверки модели с исходами)",
             description="Формат — как у PUT /snapshot, поле referenceAt обязательно и раньше текущей даты расчёта. "
                         "Текущие данные не меняются. Снимок с той же датой заменяется; демо-история удаляется.")
def add_history(request: Request, snap: SnapshotIn, user: User = Depends(require("admin"))) -> HistoryOut:
    if snap.referenceAt is None or snap.referenceAt >= request.app.state.radar.current.result.as_of:
        raise HTTPException(422, "Нужен referenceAt раньше текущей даты расчёта")
    with request.app.state.pool.connection() as conn:
        db.add_history(conn, snap.model_dump(mode="json"), "billing")
        db.log_activity(conn, "snapshot", f"Добавлен прошлый снимок на {snap.referenceAt:%d.%m.%Y}: "
                        f"{len(snap.accounts)} аккаунтов — для сверки модели с исходами", user.login)
        return HistoryOut(**db.history_info(conn))


@router.post("/events", response_model=EventResult,
             summary="Событие из системы-источника (пополнение, оплата, автопродление, смена статуса)",
             responses={404: {"description": "Аккаунт или услуга не найдены"}, 409: {"description": "Событие невозможно"}})
def post_event(request: Request, event: events.SourceEvent = Body(...), user: User = Depends(require("admin"))) -> EventResult:
    try:
        summary, version = events.apply(request.app.state.radar, event, user.login, "event")
    except simulator.SimulationError as e:
        code = 404 if "не найден" in str(e) else 409
        raise HTTPException(code, str(e)) from e
    return EventResult(summary=summary, version=version)


def demo_sender(radar):
    """Для демо-потока: событие-словарь → тот же путь, что POST /source/events."""
    return lambda event: events.apply(radar, events.event_adapter.validate_python(event), demo_stream.AUTHOR, "demo")[0]


class DemoStreamIn(BaseModel):
    running: bool
    intervalSec: int = Field(8, ge=3, le=120, description="Раз в сколько секунд событие")
    minutes: int = Field(30, ge=0, le=1440, description="Через сколько минут остановиться; 0 — без ограничения")


class DemoStreamOut(BaseModel):
    running: bool
    intervalSec: int
    eventsSent: int
    lastEvent: str | None
    stopsAt: datetime | None


def _stream_out(stream: "demo_stream.DemoStream") -> DemoStreamOut:
    stops = datetime.fromtimestamp(stream.stops_at, timezone.utc) if stream.running and stream.stops_at else None
    return DemoStreamOut(running=stream.running, intervalSec=stream.interval, eventsSent=stream.sent,
                         lastEvent=stream.last, stopsAt=stops)


@router.get("/demo-stream", response_model=DemoStreamOut, summary="Живой поток событий (демо): идёт ли и сколько отправлено")
def demo_stream_status(request: Request, _: User = Depends(current_user)) -> DemoStreamOut:
    return _stream_out(request.app.state.demo_stream)


@router.put("/demo-stream", response_model=DemoStreamOut,
            summary="Включить или выключить живой поток событий (демо, администратор)",
            description="Радар сам присылает правдоподобные события биллинга тем же путём, что POST /source/events: "
                        "пополнения, оплаты, автопродление, сигналы мониторинга и поддержки. В журнале — автор «демо-поток».")
def demo_stream_set(body: DemoStreamIn, request: Request, user: User = Depends(require("admin"))) -> DemoStreamOut:
    stream = request.app.state.demo_stream
    with request.app.state.pool.connection() as conn:
        if body.running:
            stream.start(body.intervalSec, body.minutes)
            db.log_activity(conn, "settings", f"Демо-поток событий включён: раз в {body.intervalSec} с"
                            + (f", на {body.minutes} мин" if body.minutes else ", без ограничения"), user.login)
        elif stream.running:
            stream.stop()
            db.log_activity(conn, "settings", f"Демо-поток событий выключен, отправлено: {stream.sent}", user.login)
    return _stream_out(stream)


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
