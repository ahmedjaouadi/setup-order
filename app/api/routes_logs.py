from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse


router = APIRouter()


@router.get("/logs")
async def logs_page(request: Request):
    del request
    return RedirectResponse(url="/observability#event-stream", status_code=307)


@router.get("/api/events")
async def events(
    request: Request,
    limit: int = 100,
    setup_id: str | None = None,
    symbol: str | None = None,
    level: str | None = None,
    event_type: str | None = None,
):
    return {
        "items": request.app.state.repository.list_events(
            limit=limit,
            setup_id=setup_id,
            symbol=symbol,
            level=level,
            event_type=event_type,
        )
    }


@router.get("/api/logs/tws")
async def tws_events(
    request: Request,
    limit: int = 100,
    symbol: str | None = None,
    level: str | None = None,
):
    return {
        "items": request.app.state.repository.list_events(
            limit=limit,
            symbol=symbol,
            level=level,
            event_type="tws_request",
        )
    }
