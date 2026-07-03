from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api/market-context")


@router.get("/overview")
async def market_context_overview(request: Request):
    return request.app.state.market_context.overview()


@router.get("/summary")
async def market_context_summary(request: Request):
    return request.app.state.market_context.overview()


@router.get("/heatmap")
async def market_context_heatmap(request: Request, view: str = "WATCHLIST"):
    return request.app.state.market_context.heatmap(view=view)


@router.get("/sectors")
async def market_context_sectors(request: Request):
    return request.app.state.market_context.sectors()


@router.get("/symbols/{symbol}")
async def market_context_symbol(request: Request, symbol: str):
    return request.app.state.market_context.symbol_detail(symbol)


@router.get("/events")
async def market_context_events(request: Request):
    return request.app.state.market_context.events()


@router.get("/events/{symbol}")
async def market_context_symbol_events(request: Request, symbol: str):
    return request.app.state.market_context.events(symbol=symbol)


@router.get("/economic")
async def market_context_economic(request: Request):
    return request.app.state.market_context.economic()


@router.post("/refresh")
async def market_context_refresh(request: Request):
    return request.app.state.market_context.refresh()


@router.get("/{symbol}")
async def market_context_symbol_alias(request: Request, symbol: str):
    return request.app.state.market_context.symbol_detail(symbol)
