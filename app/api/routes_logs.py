from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter()


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "logs.html",
        {"page": "logs"},
    )


@router.get("/api/events")
async def events(
    request: Request,
    limit: int = 100,
    setup_id: str | None = None,
    symbol: str | None = None,
    level: str | None = None,
):
    return {
        "items": request.app.state.repository.list_events(
            limit=limit,
            setup_id=setup_id,
            symbol=symbol,
            level=level,
        )
    }
