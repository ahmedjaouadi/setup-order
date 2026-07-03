from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.engine.broker_reality import REPORT_STATE_KEY, positions_broker_truth_overlay


router = APIRouter()


@router.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request):
    return RedirectResponse(url="/orders", status_code=307)


@router.get("/api/positions")
async def list_positions(request: Request):
    repository = request.app.state.repository
    positions = repository.list_positions()
    report = repository.get_bot_state(REPORT_STATE_KEY, {})
    return {"items": positions_broker_truth_overlay(positions, report)}


@router.post("/api/positions/{symbol}/move-stop")
async def move_stop(request: Request, symbol: str):
    payload = await request.json()
    result = await request.app.state.engine.move_stop(symbol, float(payload["new_stop"]))
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail="Stop move rejected")
    return result
