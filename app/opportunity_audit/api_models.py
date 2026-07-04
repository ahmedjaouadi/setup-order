from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReplaySetupRequestModel(BaseModel):
    config: dict[str, Any]
    initial_status: str | None = None
    enabled: bool | None = None


class ExpectedOpportunityRequestModel(BaseModel):
    setup_id: str
    expected_action: str = "ENTRY_READY"
    from_snapshot_index: int = 0
    by_snapshot_index: int | None = None
    label: str = ""


class OpportunityReplayRequestModel(BaseModel):
    setups: list[ReplaySetupRequestModel] = Field(default_factory=list)
    setup_ids: list[str] = Field(default_factory=list)
    snapshots: list[dict[str, Any]] = Field(default_factory=list)
    expected_opportunities: list[ExpectedOpportunityRequestModel] = Field(default_factory=list)
    evolve_status: bool = True
