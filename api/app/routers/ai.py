from __future__ import annotations

import re
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import ai
from app.security import current_user

router = APIRouter(prefix="/api/v1/ai", tags=["ИИ-помощник"], dependencies=[Depends(current_user)])

# Не больше двух запросов к модели одновременно — остальные получают 429, сервер не ждёт очередь.
_slots = threading.BoundedSemaphore(2)


class AIStatus(BaseModel):
    enabled: bool
    provider: str | None = Field(description="YandexGPT, GigaChat (данные в РФ) или OpenAI")
    model: str | None
    privacy: str = "В модель уходят суммы, сроки и причины; email клиентов заменяются на «клиент #ID» и наружу не передаются."


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500, examples=["Кому звонить первым и что сказать?"])


class Mention(BaseModel):
    id: int
    email: str


class AskOut(BaseModel):
    answer: str
    grounded: bool = Field(description="Все числа ответа найдены в данных Радара")
    unverified: list[str] = Field(description="Числа, которых нет в данных: ответ показывается с предупреждением")
    customers: list[Mention] = Field(description="Клиенты из ответа — ссылки на карточки (email подставляет Радар, а не модель)")
    provider: str


def client_or_503(request: Request) -> ai.AIClient:
    client = getattr(request.app.state, "ai", None)
    if client is None:
        raise HTTPException(503, "ИИ не подключён: задайте AI_PROVIDER и AI_API_KEY (см. README)")
    return client


@router.get("/status", response_model=AIStatus, summary="Подключён ли ИИ-помощник и какой моделью")
def status(request: Request) -> AIStatus:
    client = getattr(request.app.state, "ai", None)
    if client is None:
        return AIStatus(enabled=False, provider=None, model=None)
    return AIStatus(enabled=True, provider=client.title, model=client.cfg.model)


@router.post("/ask", response_model=AskOut, summary="«Спросить Радар»: ответ по данным Радара с проверкой чисел",
             description="Модель получает факты из текущего расчёта (без email) и только объясняет их. "
                         "Числа ответа сверяются с фактами; непроверенные возвращаются в unverified.",
             responses={429: {"description": "Модель занята"}, 502: {"description": "Модель недоступна"},
                        503: {"description": "ИИ не подключён"}})
def ask(body: AskIn, request: Request) -> AskOut:
    client = client_or_503(request)
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "ИИ отвечает на другой вопрос — повторите через несколько секунд")
    try:
        facts, emails = ai.gather(request.app.state.radar, body.question)
        out = ai.ask(client, ai.scrub(body.question, emails), facts)
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e
    finally:
        _slots.release()
    by_id = {v: k for k, v in emails.items()}
    ids = [int(x) for x in dict.fromkeys(re.findall(r"#(\d+)", out["answer"])) if int(x) in by_id]
    return AskOut(**out, customers=[Mention(id=i, email=by_id[i]) for i in ids], provider=client.title)
