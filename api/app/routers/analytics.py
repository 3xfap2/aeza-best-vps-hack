from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.scoring.engine import timeline
from app.security import current_user
from app.views import SummaryOut, TimelineOut, summary_out, timeline_out

router = APIRouter(prefix="/api/v1/analytics", tags=["Аналитика"], dependencies=[Depends(current_user)])


@router.get("/summary", response_model=SummaryOut,
            summary="Сводные показатели риска и денежная оценка потенциальных потерь")
def summary(request: Request) -> SummaryOut:
    cur = request.app.state.radar.current
    return summary_out(cur.result, cur.version, cur.computed_at, cur.duration_ms)


@router.get("/churn-timeline", response_model=TimelineOut,
            summary="График оттока: факт удалений и прогноз непродлений по дням")
def churn_timeline(request: Request) -> TimelineOut:
    cur = request.app.state.radar.current
    return timeline_out(cur.result, timeline(cur.result, cur.snapshot, cur.transitions))
