from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.engine.broker_reality import (
    REPORT_STATE_KEY,
    freshen_broker_reality_report,
    positions_broker_truth_overlay,
)

router = APIRouter()


class StopUpdateRequest(BaseModel):
    stop_price: float = Field(gt=0, description="New protective stop level")


@router.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request):
    return RedirectResponse(url="/orders", status_code=307)


@router.get("/api/positions")
async def list_positions(request: Request):
    repository = request.app.state.repository
    positions = repository.list_positions()
    settings = getattr(request.app.state, "settings", None)
    report = freshen_broker_reality_report(
        repository.get_bot_state(REPORT_STATE_KEY, {}),
        settings=getattr(settings, "raw", None),
    )
    return {"items": positions_broker_truth_overlay(positions, report)}


@router.post("/api/positions/{symbol}/move-stop")
async def move_stop(request: Request, symbol: str):
    payload = await request.json()
    result = await request.app.state.engine.move_stop(symbol, float(payload["new_stop"]))
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("reason") or "Stop move rejected")
    return result


@router.patch("/api/positions/{symbol}/stop")
async def update_stop(request: Request, symbol: str, payload: StopUpdateRequest):
    result = await request.app.state.engine.move_stop(symbol, payload.stop_price)
    if not result.get("ok"):
        raise HTTPException(
            status_code=422,
            detail=result.get("reason") or "Stop modification rejected",
        )
    return result
