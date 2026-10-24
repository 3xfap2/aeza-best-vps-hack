from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError

from app import backtest, db, sensitivity
from app.http import check_if_match
from app.money import to_euros
from app.scoring.engine import evaluate
from app.scoring.features import FEATURES
from app.scoring.settings import ModelSettings, diff_settings, merge_patch
from app.security import User, current_user, require
from app.source import SnapshotIn

router = APIRouter(prefix="/api/v1/model", tags=["Модель"])

PATCH_EXAMPLE = {
    "features": {"insufficient_funds": {"weight": 45}},
    "thresholds": {"high": 65},
    "horizonDays": 30,
}


class FeatureMeta(BaseModel):
    key: str
    title: str
    description: str
    defaultWeight: float
    weight: float
    enabled: bool


class SettingsOut(BaseModel):
    version: int
    settings: ModelSettings
    features: list[FeatureMeta]
    resolvedAsOf: datetime


class LevelChange(BaseModel):
    id: int
    email: str
    fromLevel: str
    toLevel: str
    fromScore: int | None
    toScore: int | None


class Totals(BaseModel):
    levels: dict[str, int]
    expectedLoss: float


class BacktestBrief(BaseModel):
    churned: int = Field(description="Клиентов, которые за горизонт не продлили хотя бы одну услугу")
    caught: int = Field(description="Из них модель заранее отнесла к высокому или среднему риску")
    falseAlarms: int = Field(description="Высокий риск, но клиент продлил")


class PreviewOut(BaseModel):
    before: Totals
    after: Totals
    changed: list[LevelChange]
    changes: list[str]
    backtestBefore: BacktestBrief | None = Field(None, description="Сверка с историей при текущих настройках")
    backtestAfter: BacktestBrief | None = Field(None, description="Сверка с историей при новых настройках")


class BacktestLevel(BaseModel):
    level: str
    customers: int
    churned: int
    actualRate: float = Field(description="Доля клиентов уровня, которые не продлили, 0–1")
    predictedRate: float = Field(description="Средняя вероятность непродления по модели, 0–1")


class BacktestFeature(BaseModel):
    feature: str
    title: str
    weight: float
    services: int = Field(description="У скольких услуг признак был на дату прошлого снимка")
    churned: int
    actualRate: float
    predictedRate: float
    verdict: Literal["ok", "under", "over", "few"] = Field(
        description="ok — прогноз совпал с фактом; under — модель недооценивает (поднять вес); "
                    "over — переоценивает (снизить вес); few — мало случаев для вывода")


class BacktestRound(BaseModel):
    asOf: datetime
    outcomeAt: datetime
    customers: int
    churned: int
    caught: int


class BacktestOut(BacktestBrief):
    available: bool
    demo: bool = Field(description="История сгенерирована для демонстрации")
    horizonDays: int
    customers: int = Field(description="Оценок клиентов на датах прошлых снимков")
    predictedLoss: float = Field(description="Ожидаемая потеря на даты прошлых снимков, €")
    actualLoss: float = Field(description="Выручка за горизонт по услугам, которые не продлили, €")
    rounds: list[BacktestRound]
    levels: list[BacktestLevel]
    features: list[BacktestFeature]


class Shift(BaseModel):
    changed: int = Field(description="Клиентов сменят уровень")
    lossPct: float = Field(description="Изменение ожидаемой потери, %")
    planSame: int = Field(description="Сколько клиентов плана звонков на день останется в плане")


class SensitivityRow(BaseModel):
    feature: str
    title: str
    weight: float
    minus: Shift = Field(description="Вес меньше на 20 %")
    plus: Shift = Field(description="Вес больше на 20 %")


class SensitivityOut(BaseModel):
    delta: float
    sampled: bool = Field(description="Посчитано на выборке клиентов (большая база)")
    customers: int
    scored: int
    planSize: int
    maxChanged: int
    maxLossPct: float
    minPlanSame: int
    rows: list[SensitivityRow]


class HistoryItem(BaseModel):
    version: int
    changes: list[str]
    author: str
    createdAt: datetime


def _settings_out(settings: ModelSettings, version: int, as_of: datetime) -> SettingsOut:
    return SettingsOut(
        version=version,
        settings=settings,
        features=[
            FeatureMeta(key=f.key, title=f.title, description=f.description, defaultWeight=f.default_weight,
                        weight=settings.features[f.key].weight, enabled=settings.features[f.key].enabled)
            for f in FEATURES
        ],
        resolvedAsOf=as_of,
    )


def _history(request: Request) -> tuple[list, bool]:
    with request.app.state.pool.connection() as conn:
        raw, info = db.history(conn), db.history_info(conn)
    return [SnapshotIn.model_validate(r).to_snapshot() for r in raw], info["demo"]


def _brief(b: backtest.Backtest) -> BacktestBrief:
    return BacktestBrief(churned=b.churned, caught=b.caught, falseAlarms=b.false_alarms)


def _apply_patch(current: ModelSettings, patch: dict[str, Any]) -> ModelSettings:
    if not isinstance(patch, dict):
        raise HTTPException(422, "Ожидается JSON-объект")
    merged = merge_patch(current.model_dump(mode="json"), patch)
    try:
        return ModelSettings.model_validate(merged)
    except ValidationError as e:
        raise HTTPException(422, [{"loc": err["loc"], "msg": err["msg"]} for err in e.errors()]) from e


@router.get("/settings", response_model=SettingsOut, summary="Действующие настройки и коэффициенты модели")
def get_settings(request: Request, response: Response, _: User = Depends(current_user)) -> SettingsOut:
    cur = request.app.state.radar.current
    response.headers["ETag"] = settings_etag(cur.settings_version)
    return _settings_out(cur.result.settings, cur.settings_version, cur.result.as_of)


def settings_etag(version: int) -> str:
    return f'"settings-{version}"'


@router.patch("/settings", response_model=SettingsOut,
              summary="Изменение настроек модели с автоматическим пересчётом результатов",
              description="Тело — JSON Merge Patch (RFC 7386): передавайте только то, что меняется. "
                          "Защита от чужой правки: заголовок If-Match с ETag из GET /settings (не совпал — 412) "
                          "или параметр expectedVersion (не совпал — 409).",
              responses={409: {"description": "expectedVersion устарел"}, 412: {"description": "If-Match устарел"}})
def patch_settings(
    request: Request,
    response: Response,
    patch: dict[str, Any] = Body(..., examples=[PATCH_EXAMPLE]),
    expectedVersion: int | None = Query(None, description="Версия, с которой начата правка; если настройки уже изменили — 409"),
    user: User = Depends(require("marketer")),
) -> SettingsOut:
    radar = request.app.state.radar
    with radar.write_lock:
        current, version = radar.settings()
        check_if_match(request, settings_etag(version))
        if expectedVersion is not None and expectedVersion != version:
            raise HTTPException(409, f"Настройки уже изменили (версия {version}, у вас {expectedVersion}). Обновите страницу и повторите.")
        new = _apply_patch(current, patch)
        changes = diff_settings(current, new)
        if changes:
            with request.app.state.pool.connection() as conn:
                db.save_settings(conn, new, changes, user.login)
                db.log_activity(conn, "settings", "; ".join(changes), user.login)
        cur = radar.recalculate("settings") if changes else radar.current
    response.headers["ETag"] = settings_etag(cur.settings_version)
    return _settings_out(cur.result.settings, cur.settings_version, cur.result.as_of)


@router.post("/preview", response_model=PreviewOut,
             summary="Предпросмотр: как изменится рейтинг и деньги, если применить настройки (без сохранения)")
def preview(
    request: Request,
    patch: dict[str, Any] = Body(..., examples=[PATCH_EXAMPLE]),
    _: User = Depends(current_user),
) -> PreviewOut:
    cur = request.app.state.radar.current
    before = cur.result
    new = _apply_patch(before.settings, patch)
    after = evaluate(cur.snapshot, new)
    history, _ = _history(request)
    bt_before = bt_after = None
    if history:
        bt_before = _brief(backtest.run(history, cur.snapshot, before.as_of, before.settings))
        bt_after = _brief(backtest.run(history, cur.snapshot, before.as_of, new))
    changed = [
        LevelChange(id=a.account.id, email=a.account.email, fromLevel=b.level, toLevel=a.level,
                    fromScore=b.score, toScore=a.score)
        for b, a in zip(before.customers, after.customers, strict=True)
        if b.level != a.level
    ]
    return PreviewOut(
        before=Totals(levels=before.summary.levels, expectedLoss=to_euros(before.summary.expected_loss)),
        after=Totals(levels=after.summary.levels, expectedLoss=to_euros(after.summary.expected_loss)),
        changed=changed,
        changes=diff_settings(before.settings, new),
        backtestBefore=bt_before,
        backtestAfter=bt_after,
    )


@router.get("/backtest", response_model=BacktestOut,
            summary="Сверка модели с исходами: прошлые снимки, оценённые текущими весами, против того, что случилось потом",
            description="Обратная связь для настройки весов. Исход услуги — её статус в первом снимке не раньше чем через "
                        "горизонт: удалена, ждёт оплаты или заблокирована — не продлена. Прошлые снимки — "
                        "POST /api/v1/source/history или каждый PUT /snapshot с referenceAt.")
def get_backtest(request: Request, _: User = Depends(current_user)) -> BacktestOut:
    cur = request.app.state.radar.current
    history, demo = _history(request)
    b = backtest.run(history, cur.snapshot, cur.result.as_of, cur.result.settings)
    rate = lambda n, d: round(n / d, 4) if d else 0.0  # noqa: E731
    return BacktestOut(
        available=bool(b.rounds), demo=demo, horizonDays=b.horizon_days, customers=b.customers,
        churned=b.churned, caught=b.caught, falseAlarms=b.false_alarms,
        predictedLoss=to_euros(b.predicted_loss), actualLoss=to_euros(b.actual_loss),
        rounds=[BacktestRound(asOf=r.as_of, outcomeAt=r.outcome_at, customers=r.customers, churned=r.churned,
                              caught=r.caught) for r in b.rounds],
        levels=[BacktestLevel(level=lvl.level, customers=lvl.customers, churned=lvl.churned,
                              actualRate=rate(lvl.churned, lvl.customers), predictedRate=round(lvl.predicted, 4))
                for lvl in b.levels.values()],
        features=[BacktestFeature(feature=f.feature, title=f.title, weight=f.weight, services=f.services, churned=f.churned,
                                  actualRate=rate(f.churned, f.services), predictedRate=round(f.predicted, 4),
                                  verdict=f.verdict) for f in b.features],
    )


@router.get("/settings/history", response_model=list[HistoryItem], summary="Журнал изменений настроек")
def history(request: Request, limit: int = Query(50, ge=1, le=500), _: User = Depends(current_user)) -> list[HistoryItem]:
    with request.app.state.pool.connection() as conn:
        return [HistoryItem(**r) for r in db.settings_history(conn, limit)]


_sensitivity_cache: dict[int, SensitivityOut] = {}


@router.get("/sensitivity", response_model=SensitivityOut,
            summary="Устойчивость к весам: что изменится, если каждый вес ошибочен на ±20 %",
            description="Для каждого признака вес сдвигается на −20 % и +20 %: сколько клиентов сменят уровень, "
                        "насколько изменится ожидаемая потеря и сколько клиентов останется в плане звонков на день. "
                        "Пересчитывается при каждом изменении данных или настроек.")
def get_sensitivity(request: Request, _: User = Depends(current_user)) -> SensitivityOut:
    cur = request.app.state.radar.current
    if cur.version not in _sensitivity_cache:
        _sensitivity_cache.clear()
        _sensitivity_cache[cur.version] = SensitivityOut(**sensitivity.run(cur.snapshot, cur.result))
    return _sensitivity_cache[cur.version]
