from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse


router = APIRouter()


@router.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "positions.html",
        {"page": "positions"},
    )


@router.get("/api/positions")
async def list_positions(request: Request):
    return {"items": request.app.state.repository.list_positions()}


@router.post("/api/positions/{symbol}/move-stop")
async def move_stop(request: Request, symbol: str):
    payload = await request.json()
    result = await request.app.state.engine.move_stop(symbol, float(payload["new_stop"]))
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail="Stop move rejected")
    return result
