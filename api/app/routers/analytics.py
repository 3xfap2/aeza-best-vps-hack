from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app import db
from app.insights import renewals
from app.scoring.engine import timeline
from app.security import current_user
from app.views import SummaryOut, TimelineOut, summary_out, timeline_out

router = APIRouter(prefix="/api/v1/analytics", tags=["Аналитика"], dependencies=[Depends(current_user)])


@router.get("/summary", response_model=SummaryOut,
            summary="Сводные показатели риска и денежная оценка потенциальных потерь")
def summary(request: Request) -> SummaryOut:
    cur = request.app.state.radar.current
    out = summary_out(cur.result, cur.version, cur.computed_at, cur.duration_ms)
    with request.app.state.pool.connection() as conn:
        last = db.last_actions(conn)
        out.retention = {"actions": db.action_counts(conn), "reasons": db.churn_reasons(conn),
                         "worked": sum(1 for c in cur.result.customers if c.level in ("high", "medium") and c.account.id in last),
                         "atRisk": sum(1 for c in cur.result.customers if c.level in ("high", "medium")),
                         "renewed": sum(1 for r in last.values() if r["kind"] == "renewed"),
                         "churned": sum(1 for r in last.values() if r["kind"] == "churned")}
    return out


@router.get("/churn-timeline", response_model=TimelineOut,
            summary="График оттока: факт удалений и прогноз непродлений по дням")
def churn_timeline(request: Request) -> TimelineOut:
    cur = request.app.state.radar.current
    return timeline_out(cur.result, timeline(cur.result, cur.snapshot, cur.transitions))


class RenewalOut(BaseModel):
    date: str
    customerId: int
    email: str
    serviceId: int
    service: str
    paymentTerm: str
    price: float
    autoProlong: bool
    funds: float
    problem: str | None
    level: str | None
    score: int | None


@router.get("/renewals", response_model=list[RenewalOut],
            summary="Календарь продлений: что продлевается в ближайшие дни и что помешает (нет денег, выключено автопродление)")
def renewal_calendar(request: Request, days: int = Query(14, ge=1, le=90)) -> list[RenewalOut]:
    cur = request.app.state.radar.current
    return [RenewalOut(**x) for x in renewals(cur.result, cur.snapshot, days)]
