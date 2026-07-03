from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.models import utc_now_iso
from app.utils.id_generator import new_id


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    entity_type: str
    entity_id: str
    decision_type: str
    decision: str
    reason_codes: list[str] = field(default_factory=list)
    symbol: str | None = None
    setup_id: str | None = None
    scenario_id: str | None = None
    opportunity_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    human_message: str = ""
    trace_id: str = field(default_factory=lambda: new_id("trace"))
    created_at: str = field(default_factory=utc_now_iso)

    def to_record(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "trace_id": self.trace_id,
            "symbol": self.symbol,
            "setup_id": self.setup_id,
            "scenario_id": self.scenario_id,
            "opportunity_id": self.opportunity_id,
            "decision_type": self.decision_type,
            "final_decision": self.decision,
            "trace": payload,
            "created_at": self.created_at,
        }
