from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request


router = APIRouter()


@router.get("/api/metrics")
async def metrics(request: Request):
    return await request.app.state.observability.metrics()


@router.get("/api/system/status")
async def system_status(request: Request):
    return await request.app.state.observability.system_status()


@router.get("/api/decision-trace")
async def decision_traces(
    request: Request,
    setup_id: str | None = None,
    symbol: str | None = None,
    scenario_id: str | None = None,
    opportunity_id: str | None = None,
    decision_type: str | None = None,
    final_decision: str | None = None,
    date: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    return {
        "items": request.app.state.observability.decision_traces(
            setup_id=setup_id,
            symbol=symbol,
            scenario_id=scenario_id,
            opportunity_id=opportunity_id,
            decision_type=decision_type,
            final_decision=final_decision,
            date=date,
            limit=limit,
        )
    }


@router.get("/api/decision-trace/setup/{setup_id}")
async def decision_traces_by_setup(
    request: Request,
    setup_id: str,
    limit: int = Query(100, ge=1, le=500),
):
    return {
        "items": request.app.state.observability.decision_traces(
            setup_id=setup_id,
            limit=limit,
        )
    }


@router.get("/api/decision-trace/symbol/{symbol}")
async def decision_traces_by_symbol(
    request: Request,
    symbol: str,
    limit: int = Query(100, ge=1, le=500),
):
    return {
        "items": request.app.state.observability.decision_traces(
            symbol=symbol,
            limit=limit,
        )
    }


@router.get("/api/decision-trace/entity/{entity_type}/{entity_id}")
async def decision_traces_by_entity(
    request: Request,
    entity_type: str,
    entity_id: str,
    limit: int = Query(100, ge=1, le=500),
):
    return {
        "items": request.app.state.observability.decision_traces_for_entity(
            entity_type,
            entity_id,
            limit=limit,
        )
    }


@router.get("/api/decision-trace/{trace_id}")
async def decision_trace(request: Request, trace_id: str):
    trace = request.app.state.observability.decision_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Decision trace not found")
    return trace


@router.get("/api/audit/daily-report")
async def daily_audit_report(request: Request):
    status = await request.app.state.observability.system_status()
    return {
        "status": status,
        "recent_events": request.app.state.repository.list_events(limit=100),
        "decision_traces": request.app.state.observability.decision_traces(limit=100),
    }
