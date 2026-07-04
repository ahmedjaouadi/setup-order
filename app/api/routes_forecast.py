from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/forecast")


@router.get("/watchlist")
async def forecast_watchlist(
    request: Request,
    timeframe: str | None = None,
):
    return request.app.state.forecast.watchlist(timeframe=timeframe)


@router.get("/{symbol}/history")
async def forecast_history(
    request: Request,
    symbol: str,
    timeframe: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe or request.app.state.forecast.config.timeframe,
        "items": request.app.state.forecast.history(
            symbol,
            timeframe=timeframe,
            limit=limit,
        ),
    }


@router.get("/{symbol}")
async def forecast_symbol(
    request: Request,
    symbol: str,
    timeframe: str | None = None,
    horizon: Annotated[int | None, Query(ge=1, le=64)] = None,
    target: str | None = None,
    setup_id: str | None = None,
    cached_only: bool = False,
    force_refresh: bool = False,
):
    forecast = await request.app.state.forecast.forecast(
        symbol,
        timeframe=timeframe,
        horizon=horizon,
        target=target,
        setup_id=setup_id,
        cached_only=cached_only,
        force_refresh=force_refresh,
    )
    return {
        "symbol": symbol.upper(),
        "timeframe": forecast.get("timeframe"),
        "forecast": forecast,
    }
