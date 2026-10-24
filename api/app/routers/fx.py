from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import fx
from app.security import current_user

router = APIRouter(prefix="/api/v1/fx", tags=["Аналитика"], dependencies=[Depends(current_user)])


class FxOut(BaseModel):
    rate: float = Field(description="Рублей за 1 евро")
    date: str = Field(description="Дата курса")
    source: str = Field(description="ЦБ РФ или вручную (FX_EUR_RUB)")
    stale: bool = Field(description="ЦБ сейчас недоступен — показан последний известный курс")


@router.get("", response_model=FxOut, summary="Курс евро к рублю (ЦБ РФ) для показа сумм в рублях",
            description="Расчёты и API остаются в евро; курс нужен только для отображения.",
            responses={503: {"description": "Курс недоступен"}})
def eur_rub() -> FxOut:
    out = fx.eur_rub()
    if out is None:
        raise HTTPException(503, "Курс ЦБ недоступен; задайте FX_EUR_RUB")
    return FxOut(**out)
