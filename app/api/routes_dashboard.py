from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models import MarketSnapshot


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
    return await request.app.state.engine.set_tws_audit_enabled(
        bool(payload.get("enabled", False))
    )


@router.post("/api/market/snapshot")
async def market_snapshot(request: Request):
    payload = await request.json()
    snapshot = MarketSnapshot(
        symbol=str(payload["symbol"]).upper(),
        price=float(payload["price"]),
        timeframe=str(payload.get("timeframe", "15m")),
        open=_float_or_none(payload.get("open")),
        high=_float_or_none(payload.get("high")),
        low=_float_or_none(payload.get("low")),
        close=_float_or_none(payload.get("close")),
        bid=_float_or_none(payload.get("bid")),
        ask=_float_or_none(payload.get("ask")),
        volume=_float_or_none(payload.get("volume")),
        current_bar_volume=_float_or_none(payload.get("current_bar_volume")),
        previous_high=_float_or_none(payload.get("previous_high")),
        daily_close=_float_or_none(payload.get("daily_close")),
        volume_ratio=_float_or_none(payload.get("volume_ratio")),
        volume_ratio_closed_bar=_float_or_none(payload.get("volume_ratio_closed_bar")),
        volume_ratio_live=_float_or_none(payload.get("volume_ratio_live")),
        average_volume_ratio_last_2_bars=_float_or_none(
            payload.get("average_volume_ratio_last_2_bars")
        ),
        elapsed_ratio=_float_or_none(payload.get("elapsed_ratio")),
        projected_volume=_float_or_none(payload.get("projected_volume")),
        bar_count=_int_or_none(payload.get("bar_count")),
        bars_above_resistance=_int_or_none(payload.get("bars_above_resistance")),
        minimum_tick=_float_or_none(payload.get("minimum_tick")),
        atr_15m=_float_or_none(payload.get("atr_15m")),
        atr_1h=_float_or_none(payload.get("atr_1h")),
        session=str(payload["session"]).upper() if payload.get("session") else None,
        market_open_time=str(payload["market_open_time"])
        if payload.get("market_open_time")
        else None,
        current_time=str(payload["current_time"]) if payload.get("current_time") else None,
        last_confirmed_higher_low=_float_or_none(
            payload.get("last_confirmed_higher_low")
        ),
        support_level=_float_or_none(payload.get("support_level")),
        successful_retest_low=_float_or_none(payload.get("successful_retest_low")),
        structural_support=_float_or_none(payload.get("structural_support")),
        breakout_already_detected=bool(payload.get("breakout_already_detected", False)),
        new_higher_low_confirmed=bool(payload.get("new_higher_low_confirmed", False)),
        close_1h=_float_or_none(payload.get("close_1h")),
        historical_bars=payload.get("historical_bars")
        if isinstance(payload.get("historical_bars"), list)
        else [],
        ema_20=_float_or_none(payload.get("ema_20")),
        ema_50=_float_or_none(payload.get("ema_50")),
        bullish_candle=bool(payload.get("bullish_candle", False)),
    )
    return await request.app.state.engine.process_market_snapshot(snapshot)


@router.get("/api/market/history/{symbol}")
async def market_history(request: Request, symbol: str, timeframe: str = "1d"):
    try:
        return await request.app.state.engine.market_history(symbol, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _float_or_none(value):
    if value in (None, ""):
        return None
    return float(value)


def _int_or_none(value):
    if value in (None, ""):
        return None
    return int(value)
