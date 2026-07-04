from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/data-quality/events")
async def data_quality_events(request: Request, symbol: str | None = None):
    return request.app.state.data_quality.events(symbol=symbol)


@router.get("/data-quality/{symbol}")
async def data_quality_symbol(request: Request, symbol: str):
    return request.app.state.data_quality.evaluate_symbol(symbol)


@router.get("/features/{symbol}")
async def features_symbol(request: Request, symbol: str):
    return request.app.state.feature_store.latest(symbol)


@router.get("/features/{symbol}/{timeframe}")
async def features_symbol_timeframe(request: Request, symbol: str, timeframe: str):
    return request.app.state.feature_store.latest(symbol, timeframe=timeframe)


@router.post("/features/{symbol}/invalidate")
async def invalidate_features(request: Request, symbol: str):
    payload = await request.json()
    return request.app.state.feature_store.invalidate(
        symbol,
        timeframe=payload.get("timeframe"),
        reason=str(payload.get("reason") or "api"),
    )


@router.get("/portfolio-risk")
async def portfolio_risk(request: Request):
    return request.app.state.portfolio_risk.analyze()


@router.get("/portfolio-risk/latest")
async def portfolio_risk_latest(request: Request):
    return request.app.state.portfolio_risk.latest()


@router.post("/portfolio-risk/size-adjustment")
async def portfolio_risk_size_adjustment(request: Request):
    payload = await request.json()
    return request.app.state.portfolio_risk.position_size_adjustment(
        str(payload.get("symbol") or ""),
        float(payload.get("requested_exposure_usd") or 0),
    )


@router.get("/runtime/events")
async def runtime_events(
    request: Request,
    event_type: str | None = None,
    symbol: str | None = None,
    aggregate_id: str | None = None,
):
    return {
        "items": request.app.state.event_bus.list_events(
            event_type=event_type,
            symbol=symbol,
            aggregate_id=aggregate_id,
        )
    }


@router.post("/runtime/events")
async def publish_runtime_event(request: Request):
    payload = await request.json()
    return request.app.state.event_bus.publish(
        str(payload.get("event_type") or "manual_event"),
        aggregate_type=payload.get("aggregate_type"),
        aggregate_id=payload.get("aggregate_id"),
        symbol=payload.get("symbol"),
        payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
        correlation_id=payload.get("correlation_id"),
        causation_id=payload.get("causation_id"),
    )
