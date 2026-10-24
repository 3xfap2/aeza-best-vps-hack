from __future__ import annotations

import re
import threading

import os

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import ai, db, quick
from app.security import User, current_user, require

router = APIRouter(prefix="/api/v1/ai", tags=["ИИ-помощник"], dependencies=[Depends(current_user)])

# Не больше двух запросов к модели одновременно — остальные получают 429, сервер не ждёт очередь.
_slots = threading.BoundedSemaphore(2)


class AIStatus(BaseModel):
    enabled: bool
    source: str = Field("off", description="panel — подключён в панели; env — переменными окружения; off — не подключён")
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
    return AIStatus(enabled=True, provider=client.title, model=client.cfg.model,
                    source=getattr(request.app.state, "ai_source", "env"))


class AIConfigOut(BaseModel):
    source: str
    baseUrl: str | None
    model: str | None
    keyHint: str | None = Field(description="Последние 4 символа ключа — сам ключ наружу не отдаётся")
    updatedBy: str | None = None
    updatedAt: str | None = None


class AIConfigIn(BaseModel):
    baseUrl: str = Field("https://api.openai.com/v1", pattern=r"^https?://\S+$", max_length=300,
                         examples=["https://api.openai.com/v1"])
    model: str = Field(min_length=1, max_length=100, examples=["gpt-4.1-mini"])
    apiKey: str | None = Field(None, min_length=8, max_length=500, description="Пусто — оставить сохранённый ключ")


def _set_client(request: Request, client: "ai.AIClient | None", source: str) -> None:
    request.app.state.ai = client
    request.app.state.ai_source = source
    bot = getattr(request.app.state, "bot", None)
    if bot is not None:
        bot.ai = client


@router.get("/config", response_model=AIConfigOut, summary="Подключение ИИ (администратор): адрес, модель, ключ скрыт")
def get_config(request: Request, _: User = Depends(require("admin"))) -> AIConfigOut:
    with request.app.state.pool.connection() as conn:
        row = db.get_config(conn, "ai")
    if row:
        v = row["value"]
        return AIConfigOut(source="panel", baseUrl=v["baseUrl"], model=v["model"], keyHint=v["apiKey"][-4:],
                           updatedBy=row["updated_by"], updatedAt=row["updated_at"].isoformat())
    client = getattr(request.app.state, "ai", None)
    if client is None:
        return AIConfigOut(source="off", baseUrl=None, model=None, keyHint=None)
    return AIConfigOut(source="env", baseUrl=client.cfg.base_url if client.cfg.provider == "openai" else client.title,
                       model=client.cfg.model, keyHint=client.cfg.api_key[-4:])


@router.put("/config", response_model=AIStatus,
            summary="Подключить OpenAI-совместимый ИИ из панели: пробный запрос, затем сохранение (администратор)",
            responses={422: {"description": "Пробный запрос не прошёл — ничего не сохранено"}})
def put_config(body: AIConfigIn, request: Request, user: User = Depends(require("admin"))) -> AIStatus:
    with request.app.state.pool.connection() as conn:
        row = db.get_config(conn, "ai")
    key = body.apiKey or (row["value"]["apiKey"] if row else None)
    if not key:
        raise HTTPException(422, "Укажите ключ API")
    value = {"baseUrl": body.baseUrl.rstrip("/"), "model": body.model.strip(), "apiKey": key.strip()}
    client = ai.AIClient(ai.config_from_panel(value, dict(os.environ)))
    try:
        client.ping()
    except ai.AIError as e:
        raise HTTPException(422, f"Пробный запрос не прошёл, ничего не сохранено: {e}") from e
    with request.app.state.pool.connection() as conn:
        db.set_config(conn, "ai", value, user.login)
        db.log_activity(conn, "settings", f"ИИ подключён: {client.title}, модель {value['model']}", user.login)
    _set_client(request, client, "panel")
    return status(request)


@router.delete("/config", status_code=204, response_class=Response,
               summary="Отключить ИИ из панели (останется подключение из переменных окружения, если есть)")
def delete_config(request: Request, user: User = Depends(require("admin"))) -> Response:
    with request.app.state.pool.connection() as conn:
        db.delete_config(conn, "ai")
        db.log_activity(conn, "settings", "ИИ из панели отключён", user.login)
    env = ai.config_from_env(dict(os.environ))
    _set_client(request, ai.AIClient(env) if env else None, "env")
    return Response(status_code=204)


class QuickIn(BaseModel):
    intent: Literal["loss", "today", "customer", "accuracy", "changes"] = Field(
        description="loss — сколько под угрозой и почему; today — кому звонить сегодня; customer — почему клиент в риске "
                    "и что делать (нужен customerId); accuracy — сверка модели с исходами; changes — что изменилось")
    customerId: int | None = Field(None, ge=1)


@router.post("/quick", response_model=AskOut,
             summary="Быстрый ответ Радара без ИИ: частые вопросы — мгновенно, прямо из расчёта",
             description="Работает и без подключённой модели. Числа берутся из того же расчёта, что панель, поэтому grounded всегда true.")
def quick_answer(body: QuickIn, request: Request) -> AskOut:
    radar = request.app.state.radar
    if body.intent == "customer":
        if body.customerId is None:
            raise HTTPException(422, "Для intent=customer нужен customerId")
        text, ids = quick.customer(radar, body.customerId)
    else:
        text, ids = getattr(quick, body.intent)(radar)
    by_id = {a.id: a.email for a in radar.current.snapshot.accounts}
    return AskOut(answer=text, grounded=True, unverified=[], provider="Радар, без ИИ",
                  customers=[Mention(id=i, email=by_id[i]) for i in dict.fromkeys(ids) if i in by_id])


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
