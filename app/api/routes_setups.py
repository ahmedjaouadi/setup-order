from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.setups.setup_factory import SetupFactory
from app.setups.text_converter import convert_text_to_setup


router = APIRouter()


@router.get("/setups", response_class=HTMLResponse)
async def setups_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "setups.html",
        {"page": "setups"},
    )


@router.get("/setups/{setup_id}", response_class=HTMLResponse)
async def setup_detail_page(request: Request, setup_id: str):
    return request.app.state.templates.TemplateResponse(
        request,
        "setup_detail.html",
        {"page": "setups", "setup_id": setup_id},
    )


@router.get("/api/setup-types")
async def setup_types():
    return {"types": SetupFactory.supported_types()}


@router.get("/api/setups")
async def list_setups(request: Request):
    return {"items": request.app.state.repository.list_setups()}


@router.get("/api/setups/{setup_id}")
async def get_setup(request: Request, setup_id: str):
    setup = request.app.state.repository.get_setup(setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="Setup not found")
    orders = request.app.state.repository.list_orders(setup_id=setup_id)
    events = request.app.state.repository.list_events(limit=100, setup_id=setup_id)
    return {"setup": setup, "orders": orders, "events": events}


@router.post("/api/setups")
async def save_setup(request: Request):
    payload = await request.json()
    result = await request.app.state.engine.save_setup(payload)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


@router.put("/api/setups/{setup_id}")
async def update_setup(request: Request, setup_id: str):
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Setup config must be a JSON object")
    payload_setup_id = str(payload.get("setup_id") or setup_id)
    if payload_setup_id != setup_id:
        raise HTTPException(
            status_code=422,
            detail="setup_id cannot be changed from the detail editor",
        )
    payload["setup_id"] = setup_id
    result = await request.app.state.engine.save_setup(payload)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


@router.post("/api/setups/convert-text")
async def convert_setup_text(request: Request):
    payload = await request.json()
    result = convert_text_to_setup(
        symbol=str(payload.get("symbol", "")),
        text=str(payload.get("text", "")),
        defaults=request.app.state.settings.raw,
        enabled=bool(payload.get("enabled", True)),
    )
    response = {
        "ok": result.ok,
        "config": result.config,
        "errors": result.errors,
        "warnings": result.warnings,
        "extracted": result.extracted,
    }
    if not result.ok:
        raise HTTPException(status_code=422, detail=response)
    validation = request.app.state.engine.setup_engine.validate_setup(result.config)
    response["validation"] = {
        "valid": validation.valid,
        "errors": validation.errors,
        "warnings": validation.warnings,
    }
    if not validation.valid:
        raise HTTPException(status_code=422, detail=response)
    return response


@router.post("/api/setups/from-text")
async def save_setup_from_text(request: Request):
    payload = await request.json()
    result = convert_text_to_setup(
        symbol=str(payload.get("symbol", "")),
        text=str(payload.get("text", "")),
        defaults=request.app.state.settings.raw,
        enabled=bool(payload.get("enabled", True)),
    )
    if not result.ok or result.config is None:
        raise HTTPException(
            status_code=422,
            detail={
                "errors": result.errors,
                "warnings": result.warnings,
                "extracted": result.extracted,
            },
        )
    saved = await request.app.state.engine.save_setup(result.config)
    if not saved.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={
                "errors": saved["errors"],
                "warnings": result.warnings,
                "extracted": result.extracted,
            },
        )
    return {
        "ok": True,
        "setup": saved["setup"],
        "config": result.config,
        "warnings": result.warnings,
        "extracted": result.extracted,
    }


@router.post("/api/setups/{setup_id}/enable")
async def enable_setup(request: Request, setup_id: str):
    try:
        return await request.app.state.engine.set_setup_enabled(setup_id, True)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")


@router.post("/api/setups/{setup_id}/disable")
async def disable_setup(request: Request, setup_id: str):
    try:
        return await request.app.state.engine.set_setup_enabled(setup_id, False)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")


@router.delete("/api/setups/{setup_id}")
async def delete_setup(request: Request, setup_id: str):
    try:
        return await request.app.state.engine.delete_setup(setup_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
