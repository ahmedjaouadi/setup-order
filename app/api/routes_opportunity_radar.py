from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/opportunity-radar", response_class=HTMLResponse)
async def opportunity_radar_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "opportunity_radar.html",
        {"page": "opportunity-radar"},
    )
