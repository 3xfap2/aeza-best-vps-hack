from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.security import current_user

router = APIRouter(prefix="/api/v1", tags=["Живое обновление"])

HEARTBEAT_SECONDS = 15


@router.get("/stream", summary="Server-Sent Events: событие recalculated после каждого пересчёта",
            response_class=StreamingResponse, dependencies=[Depends(current_user)])
async def stream(request: Request) -> StreamingResponse:
    radar = request.app.state.radar
    queue = radar.broker.subscribe()
    cur = radar.current

    async def events():
        try:
            hello = {"version": cur.version, "computedAt": cur.computed_at.isoformat()}
            yield f"retry: 3000\nevent: hello\ndata: {json.dumps(hello)}\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                if await request.is_disconnected():
                    break
        finally:
            radar.broker.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
