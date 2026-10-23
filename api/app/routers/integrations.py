from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.security import current_user

router = APIRouter(prefix="/api/v1/integrations", tags=["Интеграции"], dependencies=[Depends(current_user)])


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
