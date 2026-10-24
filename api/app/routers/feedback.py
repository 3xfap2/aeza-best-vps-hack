from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app import db
from app.http import created
from app.security import User, current_user, require

router = APIRouter(prefix="/api/v1/feedback", tags=["Отзывы о Радаре"])

KIND_TITLES = {"idea": "Идея", "problem": "Проблема", "other": "Другое"}


class FeedbackIn(BaseModel):
    kind: Literal["idea", "problem", "other"]
    text: str = Field(min_length=3, max_length=2000)
    page: str | None = Field(None, max_length=200, description="Экран, с которого отправлен отзыв")


class FeedbackItem(BaseModel):
    id: int
    author: str
    authorName: str
    kind: str
    text: str
    page: str | None
    status: Literal["new", "done"]
    createdAt: datetime


class FeedbackStatus(BaseModel):
    status: Literal["new", "done"]


@router.post("", status_code=201, response_model=FeedbackItem,
             summary="Отправить отзыв о Радаре: идея, проблема или другое (любая роль)")
def send(body: FeedbackIn, request: Request, response: Response, user: User = Depends(current_user)) -> FeedbackItem:
    text = body.text.strip()
    with request.app.state.pool.connection() as conn:
        item = db.add_feedback(conn, user.login, user.display_name, body.kind, text, body.page)
        request.app.state.radar.integrations.enqueue(conn, "feedback.created", {**item, "kindTitle": KIND_TITLES[body.kind]})
    created(response, f"/api/v1/feedback/{item['id']}")
    return FeedbackItem(**item)


@router.get("", response_model=list[FeedbackItem], summary="Отзывы сотрудников о Радаре (администратор)")
def list_all(request: Request, limit: int = Query(100, ge=1, le=500),
             _: User = Depends(require("admin"))) -> list[FeedbackItem]:
    with request.app.state.pool.connection() as conn:
        return [FeedbackItem(**r) for r in db.list_feedback(conn, limit)]


@router.patch("/{feedback_id}", status_code=204, response_class=Response,
              summary="Отметить отзыв разобранным или вернуть в новые (администратор)")
def set_status(feedback_id: int, body: FeedbackStatus, request: Request,
               _: User = Depends(require("admin"))) -> Response:
    with request.app.state.pool.connection() as conn:
        if not db.set_feedback_status(conn, feedback_id, body.status):
            raise HTTPException(404, "Отзыв не найден")
    return Response(status_code=204)
