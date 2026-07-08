from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.engine.broker_reality import (
    REPORT_STATE_KEY,
    freshen_broker_reality_report,
    orders_broker_truth_overlay,
)
from app.models import EventLevel

router = APIRouter()

DELETABLE_ORDER_STATUSES = {"REJECTED", "CANCELLED", "FILLED", "ERROR"}


class ManualOrderPayload(BaseModel):
    symbol: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z.\-]+$")
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    order_type: Literal["MKT", "LMT", "STP", "STP_LMT"]
    limit_price: float | None = Field(default=None, gt=0)
    trigger_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    allow_unprotected: bool = False


@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "orders.html",
        {"page": "orders"},
    )


@router.get("/api/orders")
async def list_orders(request: Request):
    repository = request.app.state.repository
    orders = repository.list_orders_with_protection()
    settings = getattr(request.app.state, "settings", None)
    report = freshen_broker_reality_report(
        repository.get_bot_state(REPORT_STATE_KEY, {}),
        settings=getattr(settings, "raw", None),
    )
    return {"items": orders_broker_truth_overlay(orders, report)}


@router.post("/api/orders/manual/preview")
async def manual_order_preview(request: Request, payload: ManualOrderPayload):
    """Server-side risk preview shown to the user before confirmation."""
    return await request.app.state.engine.manual_order_service.preview(payload.model_dump())


@router.post("/api/orders/manual")
async def manual_order(request: Request, payload: ManualOrderPayload):
    result = await request.app.state.engine.manual_order_service.submit(payload.model_dump())
    if result.get("validation_error"):
        raise HTTPException(status_code=400, detail=result["validation_error"])
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("block"))
    await request.app.state.engine._broadcast_snapshot()
    return result


@router.post("/api/orders/{order_id}/cancel")
async def cancel_order(request: Request, order_id: str):
    cancelled = await request.app.state.engine.order_manager.cancel_order(order_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Order cannot be cancelled")
    return {"ok": True}


@router.post("/api/orders/{order_id}/attach-stop")
async def attach_missing_stop(request: Request, order_id: str):
    try:
        stop_order = await request.app.state.engine.order_manager.attach_missing_stop(order_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.engine._broadcast_snapshot()
    return {"ok": True, "stop_order_id": stop_order.id, "status": stop_order.status}


@router.delete("/api/orders/{order_id}")
async def delete_order(request: Request, order_id: str):
    repository = request.app.state.repository
    order = repository.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(order["status"]) not in DELETABLE_ORDER_STATUSES:
        raise HTTPException(
            status_code=422,
            detail="Cancel active orders before deleting them from local history",
        )
    repository.delete_order(order_id)
    request.app.state.engine.event_store.record(
        EventLevel.INFO,
        "order_history_deleted",
        "Order removed from local history",
        setup_id=order["setup_id"],
        symbol=order["symbol"],
        data={"order_id": order_id, "status": order["status"]},
    )
    await request.app.state.engine._broadcast_snapshot()
    return {"ok": True}


@router.post("/api/orders/{order_id}/simulate-fill")
async def simulate_fill(request: Request, order_id: str):
    payload = await request.json()
    fill_price = float(payload["fill_price"])
    result = await request.app.state.engine.simulate_fill_order(order_id, fill_price)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result["reason"])
    return result
