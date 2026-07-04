from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.setups.setup_factory import SetupFactory
from app.setups.text_converter import convert_text_to_setup
from app.utils.id_generator import new_id

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


@router.get("/api/setups/config-template")
async def setup_config_template(request: Request, template_type: str = "universal"):
    return request.app.state.engine.setup_config_template(template_type)


@router.get("/api/setups/configuration-status")
async def setup_configuration_status(request: Request):
    return request.app.state.engine.configuration_status()


@router.get("/api/setups/{setup_id}")
async def get_setup(request: Request, setup_id: str):
    setup = request.app.state.repository.get_setup(setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="Setup not found")
    orders = request.app.state.repository.list_orders(setup_id=setup_id)
    events = request.app.state.repository.list_events(limit=100, setup_id=setup_id)
    return {
        "setup": setup,
        "orders": orders,
        "events": events,
        "creation_market_snapshot": request.app.state.repository.get_setup_creation_snapshot(
            setup_id
        ),
    }


@router.get("/api/setups/{setup_id}/arm-status")
async def setup_arm_status(request: Request, setup_id: str):
    try:
        return request.app.state.engine.setup_arm_status(setup_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")


@router.get("/api/setups/{setup_id}/creation-snapshot")
async def setup_creation_snapshot(request: Request, setup_id: str):
    snapshot = request.app.state.repository.get_setup_creation_snapshot(setup_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Creation snapshot not found")
    return snapshot


@router.get("/api/setups/{setup_id}/price-drift")
async def setup_price_drift(request: Request, setup_id: str):
    try:
        return request.app.state.setup_creation_snapshots.price_drift(setup_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Creation snapshot not found")


@router.post("/api/setups/{setup_id}/capture-creation-snapshot")
async def capture_setup_creation_snapshot(request: Request, setup_id: str):
    try:
        return request.app.state.setup_creation_snapshots.capture(setup_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")


@router.post("/api/setups/{setup_id}/duplicate")
async def duplicate_setup(request: Request, setup_id: str):
    source = request.app.state.repository.get_setup(setup_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Setup not found")
    payload = await request.json()
    payload = payload if isinstance(payload, dict) else {}
    config = deepcopy(source.get("config") if isinstance(source.get("config"), dict) else {})
    config.pop("creation_market_snapshot", None)
    config["setup_id"] = str(payload.get("setup_id") or new_id("setup"))
    config["symbol"] = str(payload.get("symbol") or source.get("symbol") or "").upper()
    config["enabled"] = False
    config["duplicated_from_setup_id"] = setup_id
    saved = await request.app.state.engine.save_setup(config)
    if not saved.get("ok"):
        raise HTTPException(status_code=422, detail=saved)
    snapshot = request.app.state.setup_creation_snapshots.capture(config["setup_id"])
    return {
        **saved,
        "duplicated_from_setup_id": setup_id,
        "creation_market_snapshot": snapshot,
    }


@router.post("/api/setups")
async def save_setup(request: Request):
    payload = await request.json()
    result = await request.app.state.engine.save_setup(payload)
    if not result.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={
                "errors": result["errors"],
                "warnings": result.get("warnings", []),
                "details": result.get("details", {}),
            },
        )
    result["creation_market_snapshot"] = request.app.state.setup_creation_snapshots.capture(
        str(result["setup"]["setup_id"])
    )
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
        raise HTTPException(
            status_code=422,
            detail={
                "errors": result["errors"],
                "warnings": result.get("warnings", []),
                "details": result.get("details", {}),
            },
        )
    return result


@router.post("/api/setups/{setup_id}/arm")
async def arm_setup(request: Request, setup_id: str):
    try:
        result = await request.app.state.engine.arm_setup(setup_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")
    if not result.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={
                "errors": result["errors"],
                "warnings": result.get("warnings", []),
                "details": result.get("details", {}),
            },
        )
    return result


@router.post("/api/setups/{setup_id}/disarm")
async def disarm_setup(request: Request, setup_id: str):
    try:
        return await request.app.state.engine.disarm_setup(setup_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Setup not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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
        "details": validation.details,
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
                "warnings": [*result.warnings, *saved.get("warnings", [])],
                "extracted": result.extracted,
                "details": saved.get("details", {}),
            },
        )
    creation_snapshot = request.app.state.setup_creation_snapshots.capture(
        str(saved["setup"]["setup_id"])
    )
    return {
        "ok": True,
        "setup": saved["setup"],
        "config": result.config,
        "warnings": [*result.warnings, *saved.get("warnings", [])],
        "extracted": result.extracted,
        "details": saved.get("details", {}),
        "creation_market_snapshot": creation_snapshot,
    }


@router.post("/api/setups/enable-all")
async def enable_all_setups(request: Request):
    return await request.app.state.engine.set_all_setups_enabled(True)


@router.post("/api/setups/disable-all")
async def disable_all_setups(request: Request):
    return await request.app.state.engine.set_all_setups_enabled(False)


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
