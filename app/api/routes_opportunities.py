from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/api/opportunities")
async def list_opportunities(
    request: Request,
    status: str | None = None,
    symbol: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    return {
        "items": request.app.state.opportunity_scanner.list_opportunities(
            status=status,
            symbol=symbol,
            limit=limit,
        )
    }


@router.get("/api/opportunities/top")
async def top_opportunities(request: Request, limit: int = Query(10, ge=1, le=100)):
    return {"items": request.app.state.opportunity_scanner.top(limit=limit)}


@router.get("/api/opportunities/shortlist")
async def opportunity_shortlist(request: Request, limit: int = Query(25, ge=1, le=100)):
    return request.app.state.opportunity_scanner.shortlist(limit=limit)


@router.post("/api/opportunities/rebuild-shortlist")
async def rebuild_opportunity_shortlist(request: Request):
    payload = await _optional_json(request)
    return request.app.state.opportunity_scanner.rebuild_shortlist(limit=payload.get("limit"))


@router.post("/api/opportunities/scan")
async def scan_opportunities(request: Request):
    payload = await _optional_json(request)
    return request.app.state.opportunity_scanner.scan(limit=payload.get("limit"))


@router.post("/api/opportunities/{opportunity_id}/generate-scenario")
async def generate_scenario(request: Request, opportunity_id: str):
    try:
        return request.app.state.opportunity_scanner.generate_scenario(opportunity_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Opportunity not found")


@router.post("/api/opportunities/{opportunity_id}/generate-scenario-draft")
async def generate_scenario_draft(request: Request, opportunity_id: str):
    try:
        return request.app.state.opportunity_scanner.generate_scenario_draft(opportunity_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Opportunity not found")


@router.post("/api/opportunities/{symbol}/create-setup-candidate")
async def create_setup_candidate(request: Request, symbol: str):
    try:
        return request.app.state.opportunity_scanner.create_setup_candidate(symbol)
    except KeyError:
        raise HTTPException(status_code=404, detail="Opportunity not found for symbol")


@router.post("/api/opportunities/{opportunity_id}/mark-reviewed")
async def mark_opportunity_reviewed(request: Request, opportunity_id: str):
    try:
        return request.app.state.opportunity_scanner.mark_reviewed(opportunity_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Opportunity not found")


@router.post("/api/opportunities/{opportunity_id}/expire")
async def expire_opportunity(request: Request, opportunity_id: str):
    payload = await _optional_json(request)
    try:
        return request.app.state.opportunity_scanner.expire(
            opportunity_id,
            reason=str(payload.get("reason") or "manual"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Opportunity not found")


@router.get("/api/opportunities/{opportunity_id}/explain")
async def explain_opportunity(request: Request, opportunity_id: str):
    try:
        return request.app.state.opportunity_scanner.explain(opportunity_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Opportunity not found")


@router.post("/api/opportunities/{opportunity_id}/ignore")
async def ignore_opportunity(request: Request, opportunity_id: str):
    return request.app.state.opportunity_scanner.ignore(opportunity_id)


@router.post("/api/opportunities/{opportunity_id}/archive")
async def archive_opportunity(request: Request, opportunity_id: str):
    return request.app.state.opportunity_scanner.archive(opportunity_id)


@router.get("/api/opportunities/{opportunity_id}")
async def get_opportunity(request: Request, opportunity_id: str):
    opportunity = request.app.state.opportunity_scanner.get(opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opportunity


@router.get("/api/scanner/status")
async def scanner_status(request: Request):
    return request.app.state.opportunity_scanner.status()


@router.post("/api/scanner/run")
async def scanner_run(request: Request):
    payload = await _optional_json(request)
    return request.app.state.opportunity_scanner.scan(limit=payload.get("limit"))


@router.post("/api/scanner/pause")
async def scanner_pause(request: Request):
    return request.app.state.opportunity_scanner.pause()


@router.post("/api/scanner/resume")
async def scanner_resume(request: Request):
    return request.app.state.opportunity_scanner.resume()


@router.get("/api/scanner/config")
async def scanner_config(request: Request):
    return request.app.state.opportunity_scanner.config()


@router.put("/api/scanner/config")
async def scanner_update_config(request: Request):
    payload = await request.json()
    return request.app.state.opportunity_scanner.update_config(payload)


async def _optional_json(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
