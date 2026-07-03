from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.market_data.snapshot_payload import market_snapshot_from_payload
from app.opportunity_audit import (
    ExpectedOpportunity,
    OpportunityReplayEngine,
    ReplaySetup,
)
from app.opportunity_audit.api_models import OpportunityReplayRequestModel


router = APIRouter()


@router.post("/api/opportunity-audit/replay")
async def replay_opportunity_audit(
    request: Request,
    payload: OpportunityReplayRequestModel,
):
    try:
        setups = _replay_setups_from_payload(request, payload)
        snapshots = [
            market_snapshot_from_payload(snapshot_payload)
            for snapshot_payload in payload.snapshots
        ]
        if not snapshots:
            raise ValueError("At least one market snapshot is required")
        expected_opportunities = [
            ExpectedOpportunity(
                setup_id=item.setup_id,
                expected_action=item.expected_action,
                from_snapshot_index=item.from_snapshot_index,
                by_snapshot_index=item.by_snapshot_index,
                label=item.label,
            )
            for item in payload.expected_opportunities
        ]
        settings = getattr(request.app.state, "settings", None)
        report = OpportunityReplayEngine(
            settings=settings.raw if settings is not None else None
        ).run(
            setups=setups,
            snapshots=snapshots,
            expected_opportunities=expected_opportunities,
            evolve_status=payload.evolve_status,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc.args[0]) if exc.args else "Setup not found",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "ok": True,
        "report": report.to_dict(),
    }


def _replay_setups_from_payload(
    request: Request,
    payload: OpportunityReplayRequestModel,
) -> list[ReplaySetup]:
    setups = [
        ReplaySetup(
            config=item.config,
            initial_status=item.initial_status,
            enabled=item.enabled,
        )
        for item in payload.setups
    ]
    for setup_id in payload.setup_ids:
        setup = request.app.state.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(f"Setup not found: {setup_id}")
        setups.append(
            ReplaySetup(
                config=setup["config"],
                initial_status=setup["status"],
                enabled=bool(setup["enabled"]),
            )
        )
    if not setups:
        raise ValueError("At least one setup or setup_id is required")
    return setups
