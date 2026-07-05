from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field

OPPORTUNITY_DETECTED = "OPPORTUNITY_DETECTED"
WATCHLIST_OPPORTUNITY = "WATCHLIST_OPPORTUNITY"
WEAK_OPPORTUNITY = "WEAK_OPPORTUNITY"
NO_OPPORTUNITY = "NO_OPPORTUNITY"

CREATE_SETUP_CANDIDATE_OR_WAIT_FOR_RETEST = "CREATE_SETUP_CANDIDATE_OR_WAIT_FOR_RETEST"
MONITOR_FOR_CONFIRMATION = "MONITOR_FOR_CONFIRMATION"
WAIT_FOR_RETEST = "WAIT_FOR_RETEST"
WATCHLIST_ONLY = "WATCHLIST_ONLY"
NO_ACTION = "NO_ACTION"


@dataclass(slots=True)
class OpportunitySignal:
    symbol: str
    opportunity_status: str
    opportunity_type: str
    opportunity_types: list[str]
    opportunity_score: float
    reasons: list[str]
    warnings: list[str]
    recommended_next_action: str
    can_send_order: bool = False
    discovery_score: float = 0.0
    risk_adjusted_score: float = 0.0
    badges: list[str] = field(default_factory=list)
    source_snapshot: dict[str, Any] = field(default_factory=dict)
    detected_by: str | None = None
    detected_by_techniques: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TechniqueCreateRequest(BaseModel):
    """Payload to create a manual detection technique.

    `rule` is the declarative condition tree (without `opportunity_type`);
    the service merges `opportunity_type` into it before persisting. Whitelist
    validation happens in the service so invalid rules return a 400, never
    silently at evaluation time.
    """

    name: str = Field(min_length=1, max_length=120)
    opportunity_type: str = Field(min_length=1, max_length=80)
    rule: dict[str, Any]
    description: str = ""
    enabled: bool = True


class TechniquePatchRequest(BaseModel):
    """Partial update: only supplied fields change. `rule`/`opportunity_type`
    trigger a full re-validation of the resulting rule_json."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    opportunity_type: str | None = Field(default=None, min_length=1, max_length=80)
    rule: dict[str, Any] | None = None
    description: str | None = None
    enabled: bool | None = None


class TechniqueStats(BaseModel):
    """Per-technique outcome stats. Empty until outcome tracking (P2-a) fills them."""

    sample_size: int = 0
    hit_rate: float | None = None
    avg_forward_return_pct: float | None = None
    median_forward_return_pct: float | None = None
    avg_mfe_pct: float | None = None
    avg_mae_pct: float | None = None
    expectancy_r: float | None = None
    status_label: str = "—"


class TechniqueResponse(BaseModel):
    technique_id: str
    name: str
    description: str
    rule: dict[str, Any]
    rule_summary: str
    opportunity_type: str | None
    enabled: bool
    origin: str
    parent_id: str | None
    status: str
    created_at: str
    updated_at: str
    stats: TechniqueStats
