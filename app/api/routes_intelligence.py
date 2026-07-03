from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.intelligence.api_models import (
    AnalyzeRequestModel,
    CompareAnalysesRequestModel,
    ResolveAmbiguityRequestModel,
    RollbackAnalysisRequestModel,
)


router = APIRouter()


@router.post("/api/intelligence/analyze")
async def analyze_intelligence(request: Request, payload: AnalyzeRequestModel):
    result = await request.app.state.intelligence.analyze(_model_dump(payload), persist=True)
    if not result.get("save_validation", {}).get("allowed"):
        raise HTTPException(status_code=422, detail=_structured_error_detail(result))
    return result


@router.post("/api/intelligence/validate")
async def validate_intelligence(request: Request, payload: AnalyzeRequestModel):
    result = await request.app.state.intelligence.validate(_model_dump(payload))
    if not result.get("save_validation", {}).get("allowed"):
        raise HTTPException(status_code=422, detail=_structured_error_detail(result))
    return result


@router.get("/api/intelligence/setups/{setup_id}/latest")
async def latest_setup_analysis(request: Request, setup_id: str):
    result = request.app.state.intelligence.get_latest_for_setup(setup_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result


@router.get("/api/intelligence/setups/{setup_id}/analyses")
async def list_setup_analyses(
    request: Request,
    setup_id: str,
    summary: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 8,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if summary:
        items = request.app.state.intelligence.list_summaries_for_setup(
            setup_id,
            limit=limit,
            offset=offset,
        )
        total_count = request.app.state.intelligence.count_analyses_for_setup(setup_id)
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total_count,
            "total_count": total_count,
        }
    return {"items": request.app.state.intelligence.list_for_setup(setup_id)}


@router.get("/api/intelligence/analyses/{analysis_id}")
async def get_analysis(request: Request, analysis_id: str):
    result = request.app.state.intelligence.get_analysis(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result


@router.get("/api/intelligence/analyses/{analysis_id}/scenarios")
async def list_analysis_scenarios(request: Request, analysis_id: str):
    analysis = request.app.state.intelligence.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"items": request.app.state.intelligence.get_scenarios(analysis_id)}


@router.post("/api/intelligence/setups/{setup_id}/compare")
async def compare_setup_analyses(
    request: Request,
    setup_id: str,
    payload: CompareAnalysesRequestModel,
):
    try:
        return request.app.state.intelligence.compare_analyses(
            setup_id,
            payload.left_analysis_id,
            payload.right_analysis_id,
            left_scenario_id=payload.left_scenario_id,
            right_scenario_id=payload.right_scenario_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0] if exc.args else "Analysis not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/intelligence/setups/{setup_id}/rollback")
async def rollback_setup_analysis(
    request: Request,
    setup_id: str,
    payload: RollbackAnalysisRequestModel,
):
    try:
        rollback = request.app.state.intelligence.prepare_rollback(
            setup_id,
            payload.analysis_id,
            scenario_id=payload.scenario_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0] if exc.args else "Analysis not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    saved = await request.app.state.engine.save_setup(rollback["config"])
    if not saved.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ROLLBACK_SAVE_FAILED",
                "errors": saved.get("errors", []),
                "warnings": saved.get("warnings", []),
                "details": saved.get("details", {}),
            },
        )

    history_persisted = True
    rollback_analysis = None
    history_warning = None
    try:
        rollback_analysis = await request.app.state.intelligence.record_rollback(
            setup_id,
            payload.analysis_id,
            scenario_id=payload.scenario_id,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        history_persisted = False
        history_warning = str(exc)

    return {
        "ok": True,
        "setup": saved.get("setup"),
        "warnings": saved.get("warnings", []),
        "details": saved.get("details", {}),
        "history_persisted": history_persisted,
        "history_warning": history_warning,
        "rollback_analysis": rollback_analysis,
        "restored_from": {
            "analysis_id": rollback["target_analysis"]["analysis_id"],
            "scenario_id": rollback["target_scenario"]["scenario_id"],
            "scenario_name": rollback["target_scenario"]["scenario_name"],
        },
        "comparison": rollback.get("comparison"),
    }


@router.post("/api/intelligence/analyses/{analysis_id}/ambiguities/{ambiguity_id}/resolve")
async def resolve_analysis_ambiguity(
    request: Request,
    analysis_id: str,
    ambiguity_id: str,
    payload: ResolveAmbiguityRequestModel,
):
    body = _model_dump(payload)
    resolution = {
        "selected_option": body.get("selected_option"),
        "field_value": body.get("field_value"),
        "comment": body.get("comment"),
    }
    try:
        result = await request.app.state.intelligence.resolve_ambiguity(
            analysis_id,
            ambiguity_id,
            resolution,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Ambiguity not found")
    return result


def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _structured_error_detail(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": "INTELLIGENCE_VALIDATION_FAILED",
        "analysis_id": result.get("analysis_id"),
        "save_validation": result.get("save_validation", {}),
        "arm_validation": result.get("arm_validation", {}),
        "issues": result.get("issues", []),
    }
