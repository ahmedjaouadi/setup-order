from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.post("/api/backtests/run")
async def run_backtest(request: Request):
    payload = await request.json()
    try:
        return {"backtest": request.app.state.model_lab.run_backtest(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/backtests/run-mvp")
async def run_backtest_mvp(request: Request):
    payload = await request.json()
    try:
        return {"backtest": request.app.state.model_lab.run_backtest_mvp(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/backtests")
async def list_backtests(request: Request):
    return {"items": request.app.state.model_lab.list_backtests()}


@router.get("/api/backtests/{backtest_id}")
async def get_backtest(request: Request, backtest_id: str):
    backtest = request.app.state.model_lab.get_backtest(backtest_id)
    if backtest is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return backtest


@router.get("/api/backtests/{backtest_id}/report")
async def get_backtest_report(request: Request, backtest_id: str):
    report = request.app.state.model_lab.backtest_report(backtest_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return report


@router.get("/api/backtests/{backtest_id}/events")
async def get_backtest_events(request: Request, backtest_id: str):
    if request.app.state.model_lab.get_backtest(backtest_id) is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {"items": request.app.state.model_lab.backtest_events(backtest_id)}


@router.get("/api/backtests/{backtest_id}/trades")
async def get_backtest_trades(request: Request, backtest_id: str):
    if request.app.state.model_lab.get_backtest(backtest_id) is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {"items": request.app.state.model_lab.backtest_trades(backtest_id)}


@router.get("/api/backtests/{backtest_id}/summary")
async def get_backtest_summary(request: Request, backtest_id: str):
    summary = request.app.state.model_lab.backtest_summary(backtest_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return summary


@router.post("/api/model-lab/benchmark")
async def model_lab_benchmark(request: Request):
    payload = await request.json()
    try:
        return {"benchmark": request.app.state.model_lab.benchmark(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/model-lab/benchmarks")
async def model_lab_benchmarks(request: Request, symbol: str | None = None):
    return {"items": request.app.state.model_lab.benchmarks(symbol=symbol)}


@router.get("/api/model-lab/scorecard/{symbol}")
async def model_lab_scorecard(request: Request, symbol: str):
    scorecards = request.app.state.model_lab.model_scorecards(model_name=symbol)
    if scorecards:
        return {"model_name": symbol, "items": scorecards}
    return request.app.state.model_lab.scorecard(symbol)


@router.post("/api/model-lab/run-timesfm-benchmark")
async def model_lab_run_timesfm_benchmark(request: Request):
    payload = await request.json()
    try:
        return {"scorecard": request.app.state.model_lab.run_timesfm_benchmark(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/model-lab/run-all-baselines")
async def model_lab_run_all_baselines(request: Request):
    payload = await request.json()
    try:
        return request.app.state.model_lab.run_all_baselines(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/model-lab/scorecard/{model_name}/{symbol}")
async def model_lab_scorecard_model_symbol(
    request: Request,
    model_name: str,
    symbol: str,
):
    return {
        "model_name": model_name,
        "symbol": symbol.upper(),
        "items": request.app.state.model_lab.model_scorecards(
            model_name=model_name,
            symbol=symbol,
        ),
    }


@router.get("/api/model-lab/selection-policy")
async def model_lab_selection_policy(request: Request):
    return request.app.state.model_lab.selection_policy()


@router.post("/api/model-lab/selection-policy/recompute")
async def model_lab_selection_policy_recompute(request: Request):
    return request.app.state.model_lab.recompute_selection_policy()


@router.post("/api/model-lab/forecast-stack/compare")
async def forecast_stack_compare(request: Request):
    payload = await request.json()
    try:
        return {"experiment": request.app.state.forecast_stack_benchmark.compare(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/model-lab/forecast-stack/walk-forward")
async def forecast_stack_walk_forward(request: Request):
    payload = await request.json()
    payload["validation"] = "walk_forward"
    try:
        return {"experiment": request.app.state.forecast_stack_benchmark.compare(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/model-lab/forecast-stack/run-native")
async def forecast_stack_run_native(request: Request):
    payload = await request.json()
    try:
        experiment = await asyncio.to_thread(
            request.app.state.forecast_stack_benchmark.run_native,
            payload,
        )
        return {"experiment": experiment}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/api/model-lab/forecast-stack/scorecard/{symbol}")
async def forecast_stack_scorecard(request: Request, symbol: str):
    return request.app.state.forecast_stack_benchmark.scorecard(symbol)


@router.get("/api/model-lab/forecast-stack/results")
async def forecast_stack_results(request: Request):
    return {"items": request.app.state.forecast_stack_benchmark.experiments()}


@router.get("/api/model-lab/forecast-stack/providers")
async def forecast_stack_providers(request: Request):
    return request.app.state.forecast_provider_status.list()


@router.post("/api/model-lab/darts/run-experiment")
async def darts_run_experiment(request: Request):
    payload = await request.json()
    payload["framework"] = "darts_offline"
    try:
        service = request.app.state.forecast_stack_benchmark
        operation = (
            service.compare if isinstance(payload.get("predictions"), dict) else service.run_native
        )
        return {"experiment": await asyncio.to_thread(operation, payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/api/model-lab/darts/experiments/{experiment_id}")
async def darts_experiment(request: Request, experiment_id: str):
    experiment = request.app.state.forecast_stack_benchmark.experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment
