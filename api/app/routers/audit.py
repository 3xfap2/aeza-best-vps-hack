from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app import audit
from app.security import User, require

router = APIRouter(prefix="/api/v1/audit", tags=["Журнал безопасности"])


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor: str
    action: str
    target: str | None
    ip: str | None
    requestId: str | None
    details: dict


@router.get("", response_model=list[AuditOut],
            summary="Журнал безопасности: входы (и неудачные), отказы по правам, изменения команды и модели, "
                    "выгрузки email (администратор)")
def list_audit(request: Request, action: str | None = None, actor: str | None = None,
               limit: int = Query(100, ge=1, le=500), _: User = Depends(require("admin"))) -> list[AuditOut]:
    with request.app.state.pool.connection() as conn:
        return [AuditOut(id=r["id"], at=r["at"], actor=r["actor"], action=r["action"], target=r["target"], ip=r["ip"],
                         requestId=r["request_id"], details=r["details"])
                for r in audit.recent(conn, action, actor, limit)]
