from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from app import db
from app.scoring.engine import evaluate
from app.scoring.features import FEATURES
from app.scoring.settings import ModelSettings, diff_settings, merge_patch
from app.security import User, current_user
from app.views import eur

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


class PreviewOut(BaseModel):
    before: Totals
    after: Totals
    changed: list[LevelChange]
    changes: list[str]


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


def _apply_patch(current: ModelSettings, patch: dict[str, Any]) -> ModelSettings:
    if not isinstance(patch, dict):
        raise HTTPException(422, "Ожидается JSON-объект")
    merged = merge_patch(current.model_dump(mode="json"), patch)
    try:
        return ModelSettings.model_validate(merged)
    except ValidationError as e:
        raise HTTPException(422, [{"loc": err["loc"], "msg": err["msg"]} for err in e.errors()]) from e


@router.get("/settings", response_model=SettingsOut, summary="Действующие настройки и коэффициенты модели")
def get_settings(request: Request, _: User = Depends(current_user)) -> SettingsOut:
    cur = request.app.state.radar.current
    return _settings_out(cur.result.settings, cur.settings_version, cur.result.as_of)


@router.patch("/settings", response_model=SettingsOut,
              summary="Изменение настроек модели с автоматическим пересчётом результатов",
              description="Тело — JSON Merge Patch (RFC 7386): передавайте только то, что меняется.")
def patch_settings(
    request: Request,
    patch: dict[str, Any] = Body(..., examples=[PATCH_EXAMPLE]),
    user: User = Depends(current_user),
) -> SettingsOut:
    radar = request.app.state.radar
    with radar.write_lock:
        current, _ = radar.settings()
        new = _apply_patch(current, patch)
        changes = diff_settings(current, new)
        if changes:
            with request.app.state.pool.connection() as conn:
                db.save_settings(conn, new, changes, user.login)
                db.log_activity(conn, "settings", "; ".join(changes), user.login)
        cur = radar.recalculate("settings") if changes else radar.current
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
    changed = [
        LevelChange(id=a.account.id, email=a.account.email, fromLevel=b.level, toLevel=a.level,
                    fromScore=b.score, toScore=a.score)
        for b, a in zip(before.customers, after.customers)
        if b.level != a.level
    ]
    return PreviewOut(
        before=Totals(levels=before.summary.levels, expectedLoss=eur(before.summary.expected_loss)),
        after=Totals(levels=after.summary.levels, expectedLoss=eur(after.summary.expected_loss)),
        changed=changed,
        changes=diff_settings(before.settings, new),
    )


@router.get("/settings/history", response_model=list[HistoryItem], summary="Журнал изменений настроек")
def history(request: Request, limit: int = Query(50, ge=1, le=500), _: User = Depends(current_user)) -> list[HistoryItem]:
    with request.app.state.pool.connection() as conn:
        return [HistoryItem(**r) for r in db.settings_history(conn, limit)]
