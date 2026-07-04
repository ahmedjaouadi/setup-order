from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.market_data.snapshot_payload import market_snapshot_from_payload

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "index.html",
        {"page": "dashboard"},
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_alias(request: Request):
    return await dashboard_page(request)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "settings.html",
        {"page": "settings"},
    )


@router.get("/api/dashboard")
async def dashboard_data(request: Request):
    return await request.app.state.engine.snapshot()


@router.get("/api/equity/history")
async def equity_history(request: Request, limit: int = 500):
    limit = max(2, min(int(limit or 500), 2000))
    rows = request.app.state.repository.list_equity_snapshots(limit=limit)
    points = [
        {
            "t": row.get("captured_at"),
            "equity": row.get("net_liquidation"),
            "daily_pnl": row.get("daily_pnl"),
            "positions_pnl": row.get("positions_pnl"),
            "open_positions": row.get("open_positions"),
            "source": row.get("source"),
        }
        for row in rows
        if row.get("net_liquidation") is not None
    ]
    first = points[0]["equity"] if points else None
    last = points[-1]["equity"] if points else None
    return {
        "points": points,
        "count": len(points),
        "first_equity": first,
        "last_equity": last,
        "change": (round(last - first, 2) if first is not None and last is not None else None),
        "change_pct": (
            round((last - first) / first * 100, 2)
            if first not in (None, 0) and last is not None
            else None
        ),
    }


@router.post("/api/runtime/emergency-stop")
async def emergency_stop(request: Request):
    return await request.app.state.engine.emergency_stop()


@router.post("/api/runtime/pause")
async def pause(request: Request):
    return await request.app.state.engine.pause()


@router.post("/api/runtime/resume")
async def resume(request: Request):
    return await request.app.state.engine.resume()


@router.post("/api/runtime/sync")
async def force_sync(request: Request):
    return await request.app.state.engine.force_sync()


@router.post("/api/runtime/broker-connector")
async def broker_connector(request: Request):
    payload = await request.json()
    connector = str(payload.get("connector", "")).strip().lower()
    host = str(payload.get("host", "")).strip() or None
    port = _int_or_none(payload.get("port"))
    client_id = _int_or_none(payload.get("client_id"))
    try:
        return await request.app.state.engine.set_broker_connector(
            connector,
            host=host,
            port=port,
            client_id=client_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/runtime/tws-audit")
async def tws_audit(request: Request):
    payload = await request.json()
    return await request.app.state.engine.set_tws_audit_enabled(bool(payload.get("enabled", False)))


@router.post("/api/market/snapshot")
async def market_snapshot(request: Request):
    payload = await request.json()
    try:
        snapshot = market_snapshot_from_payload(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return await request.app.state.engine.process_market_snapshot(snapshot)


@router.get("/api/market-data/{symbol}/diagnostics")
async def market_data_diagnostics(request: Request, symbol: str):
    normalized_symbol = symbol.upper()
    broker = request.app.state.engine.broker
    diagnostics = getattr(broker, "market_data_diagnostics", None)
    broker_payload = {}
    if callable(diagnostics):
        broker_payload = diagnostics(symbol)
    latest = request.app.state.engine.market_data.latest(symbol)
    latest_payload = (
        request.app.state.engine._market_snapshot_payload(latest) if latest is not None else None
    )
    setup: dict[str, Any] = next(
        (
            item
            for item in request.app.state.repository.list_setups()
            if str(item.get("symbol", "")).upper() == normalized_symbol
        ),
        {},
    )
    readiness = (
        latest_payload.get("market_data_readiness", {}) if isinstance(latest_payload, dict) else {}
    )
    summary = _market_diagnostics_summary(
        normalized_symbol,
        setup,
        latest_payload,
        readiness,
        broker_payload,
    )
    return {
        **broker_payload,
        **summary,
        "live_quote": latest_payload,
        "message": (
            broker_payload.get("message")
            or ("" if callable(diagnostics) else "Broker diagnostics are not available.")
        ),
    }


@router.get("/api/market/history/{symbol}")
async def market_history(request: Request, symbol: str, timeframe: str = "1d"):
    try:
        return await request.app.state.engine.market_history(symbol, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _int_or_none(value):
    if value in (None, ""):
        return None
    return int(value)


def _market_diagnostics_summary(
    symbol: str,
    setup: dict,
    latest_payload: dict | None,
    readiness: dict,
    broker_payload: dict,
) -> dict:
    latest_payload = latest_payload or {}
    missing = readiness.get("missing") if isinstance(readiness, dict) else []
    last_error_message = (
        latest_payload.get("last_ibkr_error_message")
        or broker_payload.get("last_ibkr_error_message")
        or broker_payload.get("last_ibkr_error")
    )
    return {
        "symbol": symbol,
        "setup_id": setup.get("setup_id"),
        "market_data_source": latest_payload.get("market_data_source"),
        "live_quote_source": latest_payload.get("live_quote_source"),
        "market_data_type_requested": latest_payload.get("market_data_type_requested"),
        "market_data_type_actual": latest_payload.get("market_data_type_actual"),
        "live_market_data_status": latest_payload.get("live_market_data_status"),
        "bid": latest_payload.get("bid"),
        "ask": latest_payload.get("ask"),
        "spread": latest_payload.get("spread"),
        "atr_15m": latest_payload.get("atr_15m"),
        "atr_1h": latest_payload.get("atr_1h"),
        "atr_1h_status": latest_payload.get("atr_1h_status"),
        "atr_1h_bar_size": latest_payload.get("atr_1h_bar_size"),
        "atr_1h_duration": latest_payload.get("atr_1h_duration"),
        "atr_1h_use_rth": latest_payload.get("atr_1h_use_rth"),
        "bars_15m_count": latest_payload.get("bars_15m_count"),
        "bars_1h_count": latest_payload.get("bars_1h_count"),
        "bars_required_for_atr": latest_payload.get("bars_required_for_atr"),
        "historical_1h_available": latest_payload.get("historical_1h_available"),
        "historical_1h_error": latest_payload.get("historical_1h_error"),
        "last_successful_atr_1h": latest_payload.get("last_successful_atr_1h"),
        "last_successful_atr_1h_at": latest_payload.get("last_successful_atr_1h_at"),
        "atr_1h_age_seconds": latest_payload.get("atr_1h_age_seconds"),
        "last_ibkr_error_code": (
            latest_payload.get("last_ibkr_error_code") or broker_payload.get("last_ibkr_error_code")
        ),
        "last_ibkr_error_message": last_error_message,
        "readiness_level": readiness.get("status") if isinstance(readiness, dict) else None,
        "missing_fields": missing if isinstance(missing, list) else [],
    }
