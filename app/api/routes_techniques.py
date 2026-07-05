from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.opportunity_scanner.schemas import (
    TechniqueCreateRequest,
    TechniquePatchRequest,
)
from app.opportunity_scanner.technique_service import (
    InvalidRuleError,
    TechniqueNotFoundError,
    TechniqueService,
)

router = APIRouter()


def _service(request: Request) -> TechniqueService:
    return request.app.state.techniques


@router.get("/api/techniques")
async def list_techniques(request: Request):
    return {"items": _service(request).list_techniques()}


@router.post("/api/techniques")
async def create_technique(request: Request, payload: TechniqueCreateRequest):
    try:
        return _service(request).create_technique(payload)
    except InvalidRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/techniques/{technique_id}")
async def get_technique(request: Request, technique_id: str):
    try:
        return _service(request).get_technique(technique_id)
    except TechniqueNotFoundError:
        raise HTTPException(status_code=404, detail="Technique not found")


@router.patch("/api/techniques/{technique_id}")
async def patch_technique(request: Request, technique_id: str, payload: TechniquePatchRequest):
    try:
        return _service(request).update_technique(technique_id, payload)
    except TechniqueNotFoundError:
        raise HTTPException(status_code=404, detail="Technique not found")
    except InvalidRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/techniques/{technique_id}")
async def delete_technique(request: Request, technique_id: str):
    try:
        return _service(request).retire_technique(technique_id)
    except TechniqueNotFoundError:
        raise HTTPException(status_code=404, detail="Technique not found")


@router.get("/api/techniques/{technique_id}/outcomes")
async def technique_outcomes(request: Request, technique_id: str):
    try:
        return {"items": _service(request).list_outcomes(technique_id)}
    except TechniqueNotFoundError:
        raise HTTPException(status_code=404, detail="Technique not found")


@router.post("/api/techniques/learning/run")
async def run_learning(request: Request):
    # Learning loop is implemented in P2-b. The route exists now so the contract
    # is stable, but forcing a cycle is a no-op until then.
    raise HTTPException(status_code=501, detail="Learning loop is not implemented yet (P2-b)")
