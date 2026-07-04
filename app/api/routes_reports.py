from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/reports")


@router.post("/daily/generate")
async def generate_daily_report(request: Request):
    payload = await _optional_json(request)
    return request.app.state.daily_reports.generate(
        report_date=payload.get("date") or payload.get("report_date")
    )


@router.get("/daily/latest")
async def latest_daily_report(request: Request):
    report = request.app.state.daily_reports.latest()
    if report is None:
        raise HTTPException(status_code=404, detail="Daily report not found")
    return report


@router.get("/daily/{report_date}")
async def daily_report_by_date(request: Request, report_date: str):
    report = request.app.state.daily_reports.get(report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="Daily report not found")
    return report


async def _optional_json(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
