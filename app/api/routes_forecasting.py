from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/forecasting")


@router.post("/run")
async def run_forecasting(request: Request):
    payload = await request.json()
    symbol = str(payload.get("symbol") or "").upper()
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol is required")
    if payload.get("models") or payload.get("ensemble", True):
        return {
            "forecast": await request.app.state.forecast.forecast_ensemble(
                symbol,
                timeframe=payload.get("timeframe"),
                horizon=payload.get("horizon"),
                target=payload.get("target"),
                setup_id=payload.get("setup_id"),
                models=payload.get("models"),
                force_refresh=bool(payload.get("force_refresh", False)),
            )
        }
    return {
        "forecast": await request.app.state.forecast.forecast(
            symbol,
            timeframe=payload.get("timeframe"),
            horizon=payload.get("horizon"),
            target=payload.get("target"),
            setup_id=payload.get("setup_id"),
            force_refresh=bool(payload.get("force_refresh", False)),
            model_name=payload.get("model_name"),
        )
    }


@router.post("/run-for-scenario/{scenario_id}")
async def run_forecasting_for_scenario(request: Request, scenario_id: str):
    payload = await request.json()
    payload["scenario_id"] = scenario_id
    result = await run_forecasting(request)
    result["scenario_id"] = scenario_id
    return result


@router.post("/run-for-setup/{setup_id}")
async def run_forecasting_for_setup(request: Request, setup_id: str):
    payload = await request.json()
    setup = request.app.state.repository.get_setup(setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="Setup not found")
    config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
    timeframe = payload.get("timeframe")
    if timeframe is None:
        timeframes = config.get("timeframes") if isinstance(config.get("timeframes"), dict) else {}
        timeframe = timeframes.get("signal") or config.get("timeframe")
    forecast = await request.app.state.forecast.forecast(
        str(setup.get("symbol") or ""),
        timeframe=timeframe,
        horizon=payload.get("horizon"),
        target=payload.get("target"),
        setup_id=setup_id,
        cached_only=bool(payload.get("cached_only", False)),
        force_refresh=bool(payload.get("force_refresh", False)),
        model_name=payload.get("model_name"),
    )
    forecast["used_for_decision"] = False
    forecast["decision_impact"] = "SCORING_ONLY" if forecast.get("status") == "OK" else "NONE"
    return {"setup_id": setup_id, "forecast": forecast}


@router.get("/models")
async def forecast_models(request: Request):
    return request.app.state.forecast.models()


@router.get("/providers")
async def forecast_provider_statuses(request: Request):
    return request.app.state.forecast_provider_status.list()


@router.post("/providers/{model_name}/test")
async def test_forecast_provider(request: Request, model_name: str):
    payload = await request.json()
    symbol = str(payload.get("symbol") or "").upper()
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol is required")
    forecast = await request.app.state.forecast.forecast(
        symbol,
        timeframe=payload.get("timeframe"),
        horizon=payload.get("horizon_bars"),
        model_name=model_name,
        force_refresh=True,
    )
    return {"model_name": model_name, "forecast": forecast, "used_for_execution": False}


@router.get("/providers/{model_name}/forecasts")
async def provider_forecasts(request: Request, model_name: str, limit: int = 20):
    persisted_name = request.app.state.forecast._persisted_model_name(model_name)
    return {
        "model_name": model_name,
        "items": request.app.state.forecast_repository.history_for_model(
            persisted_name,
            limit=max(1, min(limit, 200)),
        ),
    }


@router.get("/providers/{model_name}/errors")
async def provider_errors(request: Request, model_name: str, limit: int = 20):
    persisted_name = request.app.state.forecast._persisted_model_name(model_name)
    rows = request.app.state.forecast_repository.history_for_model(
        persisted_name,
        limit=max(1, min(limit * 5, 500)),
    )
    return {
        "model_name": model_name,
        "items": [row for row in rows if row.get("status") not in {"OK", "PARTIAL"}][:limit],
    }


@router.post("/outcomes/evaluate-due")
async def evaluate_due_forecast_outcomes(request: Request):
    payload = await request.json()
    prices = payload.get("prices") if isinstance(payload.get("prices"), dict) else {}
    return request.app.state.forecast_accuracy.evaluate_due(
        prices,
        evaluated_at=payload.get("evaluated_at"),
    )


@router.post("/scorecards/rebuild")
async def rebuild_forecast_accuracy_scorecards(request: Request):
    return {"items": request.app.state.forecast_accuracy.rebuild_scorecards()}


@router.get("/accuracy/{model_name}")
async def forecast_accuracy(
    request: Request,
    model_name: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    horizon_bars: int | None = None,
):
    return {
        "model_name": model_name,
        "items": request.app.state.forecast_accuracy.scorecards(
            model_name,
            symbol=symbol,
            timeframe=timeframe,
            horizon_bars=horizon_bars,
        ),
        "last_forecasts": request.app.state.forecast_accuracy.outcomes(
            model_name,
            symbol=symbol,
            timeframe=timeframe,
            horizon_bars=horizon_bars,
            limit=10,
        ),
    }


@router.get("/accuracy/{model_name}/{symbol}")
async def forecast_accuracy_symbol(request: Request, model_name: str, symbol: str):
    return await forecast_accuracy(request, model_name, symbol=symbol)


@router.get("/accuracy/{model_name}/{symbol}/{timeframe}")
async def forecast_accuracy_symbol_timeframe(
    request: Request, model_name: str, symbol: str, timeframe: str
):
    return await forecast_accuracy(request, model_name, symbol=symbol, timeframe=timeframe)


@router.get("/accuracy/{model_name}/{symbol}/{timeframe}/{horizon_bars}")
async def forecast_accuracy_symbol_timeframe_horizon(
    request: Request,
    model_name: str,
    symbol: str,
    timeframe: str,
    horizon_bars: int,
):
    return await forecast_accuracy(
        request,
        model_name,
        symbol=symbol,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
    )


@router.get("/outcomes/{model_name}")
async def forecast_outcomes(
    request: Request,
    model_name: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    status: str | None = None,
    limit: int = 50,
):
    return {
        "model_name": model_name,
        "items": request.app.state.forecast_accuracy.outcomes(
            model_name,
            symbol=symbol,
            timeframe=timeframe,
            status=status,
            limit=max(1, min(limit, 500)),
        ),
    }


@router.get("/scorecards/{model_name}/{symbol}")
async def forecast_scorecards(request: Request, model_name: str, symbol: str):
    return {
        "model_name": model_name,
        "symbol": symbol.upper(),
        "items": request.app.state.forecast_accuracy.scorecards(
            model_name,
            symbol=symbol,
        ),
    }


@router.get("/symbol/{symbol}")
async def forecast_symbol_history(request: Request, symbol: str):
    return {
        "symbol": symbol.upper(),
        "items": request.app.state.forecast.history(symbol),
    }


@router.get("/stack-summary/{symbol}")
async def forecast_stack_summary(
    request: Request,
    symbol: str,
    timeframe: str | None = None,
    setup_id: str | None = None,
):
    return request.app.state.forecast.stack_summary(
        symbol,
        timeframe=timeframe,
        setup_id=setup_id,
    )


@router.get("/ensemble/{ensemble_id}")
async def forecast_ensemble(request: Request, ensemble_id: str):
    ensemble = request.app.state.forecast_repository.get_ensemble(ensemble_id)
    if ensemble is None:
        raise HTTPException(status_code=404, detail="Forecast ensemble not found")
    return ensemble


@router.get("/{forecast_id}")
async def forecast_by_id(request: Request, forecast_id: str):
    try:
        forecast = request.app.state.forecast_repository.get_forecast_metric(forecast_id)
    except ValueError:
        forecast = None
    if forecast is None:
        raise HTTPException(status_code=404, detail="Forecast not found")
    return forecast
