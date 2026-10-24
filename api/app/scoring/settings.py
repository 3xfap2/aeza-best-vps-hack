from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .features import FEATURES


class FeatureSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weight: float = Field(ge=0, le=100, description="Сколько баллов даёт признак при силе 1")
    enabled: bool = True


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    high: float = Field(60, gt=0, le=100, description="Score не ниже — высокий риск")
    medium: float = Field(30, gt=0, le=100, description="Score не ниже — средний риск")

    @model_validator(mode="after")
    def _ordered(self) -> "Thresholds":
        if self.medium >= self.high:
            raise ValueError("Порог высокого риска должен быть больше порога среднего")
        return self


class AsOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["snapshot", "now", "fixed"] = Field(
        "snapshot",
        description="snapshot — момент снимка (referenceAt или последняя дата в данных), now — текущее время, fixed — value",
    )
    value: datetime | None = None

    @model_validator(mode="after")
    def _value_for_fixed(self) -> "AsOf":
        if self.mode == "fixed" and self.value is None:
            raise ValueError("Для mode=fixed нужна дата value")
        if self.value is not None and self.value.tzinfo is None:
            raise ValueError("Дата должна быть с часовым поясом")
        return self


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: dict[str, FeatureSetting]
    thresholds: Thresholds = Thresholds()
    horizonDays: int = Field(30, ge=1, le=365, description="Горизонт прогноза и денежной оценки, дней")
    ltvMonthlyChurn: float = Field(5, ge=0.5, le=50, description="Базовый месячный отток для LTV, %: LTV = месячная выручка / отток")
    recalcMinutes: int = Field(5, ge=1, le=1440, description="Периодичность прогноза: плановый пересчёт раз в N минут (кроме пересчётов на каждое изменение)")
    asOf: AsOf = AsOf()

    @model_validator(mode="after")
    def _known_features(self) -> "ModelSettings":
        known = {f.key for f in FEATURES}
        unknown = set(self.features) - known
        if unknown:
            raise ValueError(f"Неизвестные признаки: {', '.join(sorted(unknown))}")
        for f in FEATURES:  # новый признак получает вес по умолчанию
            self.features.setdefault(f.key, FeatureSetting(weight=f.default_weight))
        return self

    def weight(self, key: str) -> float:
        fs = self.features[key]
        return fs.weight if fs.enabled else 0.0


def default_settings() -> ModelSettings:
    return ModelSettings(features={f.key: FeatureSetting(weight=f.default_weight) for f in FEATURES})


def merge_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """JSON Merge Patch (RFC 7386): вложенные объекты сливаются, null удаляет ключ."""
    result = dict(base)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_patch(result[key], value)
        else:
            result[key] = value
    return result


def diff_settings(old: ModelSettings, new: ModelSettings) -> list[str]:
    """Человеческое описание изменений для журнала."""
    titles = {f.key: f.title for f in FEATURES}
    changes: list[str] = []
    for key, fs in new.features.items():
        before = old.features.get(key)
        if before is None or before.weight != fs.weight:
            changes.append(f"{titles[key]}: вес {_num(before.weight) if before else '—'} → {_num(fs.weight)}")
        if before is not None and before.enabled != fs.enabled:
            changes.append(f"{titles[key]}: {'включён' if fs.enabled else 'выключен'}")
    if old.thresholds != new.thresholds:
        changes.append(
            f"Пороги: высокий {_num(old.thresholds.high)} → {_num(new.thresholds.high)}, "
            f"средний {_num(old.thresholds.medium)} → {_num(new.thresholds.medium)}"
        )
    if old.horizonDays != new.horizonDays:
        changes.append(f"Горизонт: {old.horizonDays} → {new.horizonDays} дн.")
    if old.ltvMonthlyChurn != new.ltvMonthlyChurn:
        changes.append(f"Базовый отток для LTV: {_num(old.ltvMonthlyChurn)} → {_num(new.ltvMonthlyChurn)} %")
    if old.recalcMinutes != new.recalcMinutes:
        changes.append(f"Плановый пересчёт: раз в {old.recalcMinutes} → {new.recalcMinutes} мин")
    if old.asOf != new.asOf:
        changes.append(f"Дата расчёта: {_as_of(old.asOf)} → {_as_of(new.asOf)}")
    return changes


def _num(v: float) -> str:
    return f"{v:g}"


def _as_of(a: AsOf) -> str:
    if a.mode == "fixed" and a.value:
        return a.value.strftime("%d.%m.%Y %H:%M")
    return {"snapshot": "дата снимка", "now": "сейчас"}.get(a.mode, a.mode)
