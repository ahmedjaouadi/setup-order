from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
