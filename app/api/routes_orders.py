from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models import EventLevel


router = APIRouter()

DELETABLE_ORDER_STATUSES = {"REJECTED", "CANCELLED", "FILLED", "ERROR"}


@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "orders.html",
        {"page": "orders"},
    )


@router.get("/api/orders")
async def list_orders(request: Request):
    return {"items": request.app.state.repository.list_orders()}


@router.post("/api/orders/{order_id}/cancel")
async def cancel_order(request: Request, order_id: str):
    cancelled = await request.app.state.engine.order_manager.cancel_order(order_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Order cannot be cancelled")
    return {"ok": True}


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
