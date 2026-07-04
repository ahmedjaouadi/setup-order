from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/scoring")


@router.post("/score-opportunity/{opportunity_id}")
async def score_opportunity(request: Request, opportunity_id: str):
    try:
        return request.app.state.scoring.score_opportunity(opportunity_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Opportunity not found")


@router.post("/score-scenario/{scenario_id}")
async def score_scenario(request: Request, scenario_id: str):
    return request.app.state.scoring.score_scenario(scenario_id)


@router.post("/score-setup/{setup_id}")
async def score_setup(request: Request, setup_id: str):
    try:
        return request.app.state.scoring.score_setup(setup_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")


@router.get("/symbol/{symbol}")
async def scores_by_symbol(request: Request, symbol: str):
    return {"items": request.app.state.scoring.latest_scores(symbol=symbol)}


@router.get("/{score_id}")
async def score_by_id(request: Request, score_id: str):
    score = request.app.state.repository.get_setup_score(score_id)
    if score is None:
        raise HTTPException(status_code=404, detail="Score not found")
    return score
