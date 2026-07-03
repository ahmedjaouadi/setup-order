from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter()


@router.get("/observability", response_class=HTMLResponse)
async def observability_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "observability.html",
        {"page": "observability"},
    )


@router.get("/research", response_class=HTMLResponse)
async def research_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "research.html",
        {"page": "research"},
    )


@router.get("/opportunities", response_class=HTMLResponse)
async def opportunities_page(request: Request):
    del request
    return RedirectResponse(url="/opportunity-radar#opportunity-pipeline", status_code=307)


@router.get("/scanner", response_class=HTMLResponse)
async def scanner_page(request: Request):
    del request
    return RedirectResponse(url="/opportunity-radar#scanner-control", status_code=307)


@router.get("/opportunity_scanner", response_class=HTMLResponse)
async def opportunity_scanner_page(request: Request):
    del request
    return RedirectResponse(url="/opportunity-radar#opportunity-pipeline", status_code=307)


@router.get("/radar", response_class=HTMLResponse)
async def radar_page(request: Request):
    del request
    return RedirectResponse(url="/opportunity-radar", status_code=307)


@router.get("/market-context", response_class=HTMLResponse)
async def market_context_page(request: Request):
    del request
    return RedirectResponse(url="/opportunity-radar#market-context", status_code=307)


@router.get("/model-lab", response_class=HTMLResponse)
async def model_lab_page(request: Request):
    del request
    return RedirectResponse(url="/research#benchmarks", status_code=307)


@router.get("/backtests", response_class=HTMLResponse)
async def backtests_page(request: Request):
    del request
    return RedirectResponse(url="/research#backtests", status_code=307)


@router.get("/forecasting", response_class=HTMLResponse)
async def forecasting_page(request: Request):
    del request
    return RedirectResponse(url="/research#model-catalog", status_code=307)


@router.get("/forecasting/stack", response_class=HTMLResponse)
async def forecasting_stack_page(request: Request):
    del request
    return RedirectResponse(url="/research#provider-stack", status_code=307)


@router.get("/model-lab/forecast-stack", response_class=HTMLResponse)
async def model_lab_forecast_stack_page(request: Request):
    del request
    return RedirectResponse(url="/research#experiments", status_code=307)


@router.get("/decision-trace", response_class=HTMLResponse)
async def decision_trace_page(request: Request):
    del request
    return RedirectResponse(url="/observability#decision-traces", status_code=307)


@router.get("/system-health", response_class=HTMLResponse)
async def system_health_page(request: Request):
    del request
    return RedirectResponse(url="/observability#runtime-health", status_code=307)
