from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app import integrations
from app.security import User, current_user, require

router = APIRouter(prefix="/api/v1/integrations", tags=["Интеграции"], dependencies=[Depends(current_user)])

Status = Literal["delivered", "pending", "failed"]


class DeliveryOut(BaseModel):
    id: int
    sink: str
    type: str
    status: Status
    attempts: int
    lastError: str | None
    createdAt: datetime
    nextAttemptAt: datetime
    deliveredAt: datetime | None
    payload: dict


def _delivery(r: dict) -> DeliveryOut:
    return DeliveryOut(id=r["id"], sink=r["sink"], type=r["type"], status=r["status"], attempts=r["attempts"],
                       lastError=r["last_error"], createdAt=r["created_at"], nextAttemptAt=r["next_attempt_at"],
                       deliveredAt=r["delivered_at"], payload=r["payload"])


@router.get("/deliveries", response_model=list[DeliveryOut],
            summary="Журнал доставок во внешние системы (CRM, вебхук): что дошло, что ждёт повтора, что сдалось (администратор)")
def deliveries(request: Request, status: Status | None = None, limit: int = Query(50, ge=1, le=500),
               _: User = Depends(require("admin"))) -> list[DeliveryOut]:
    with request.app.state.pool.connection() as conn:
        return [_delivery(r) for r in integrations.list_deliveries(conn, status, limit)]


@router.post("/deliveries/{delivery_id}/redeliver", status_code=202, response_model=DeliveryOut,
             summary="Отправить событие заново: например, после того как CRM снова доступна (администратор)")
def redeliver(delivery_id: int, request: Request, _: User = Depends(require("admin"))) -> DeliveryOut:
    with request.app.state.pool.connection() as conn:
        row = integrations.redeliver(conn, delivery_id)
    if row is None:
        raise HTTPException(404, "Доставка не найдена")
    return _delivery(row)


class TelegramOut(BaseModel):
    enabled: bool
    botUsername: str | None
    connectUrl: str | None


@router.get("/telegram", response_model=TelegramOut,
            summary="Telegram-бот: ссылка для подключения уведомлений (только для вошедших пользователей)")
def telegram(request: Request) -> TelegramOut:
    bot = getattr(request.app.state, "bot", None)
    if bot is None or not bot.username:
        return TelegramOut(enabled=bot is not None, botUsername=None, connectUrl=None)
    return TelegramOut(enabled=True, botUsername=bot.username,
                       connectUrl=f"https://t.me/{bot.username}?start={bot.link_code}")
