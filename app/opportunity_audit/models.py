from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import MarketSnapshot, SetupStatus, SignalAction, to_jsonable


@dataclass(slots=True)
class ReplaySetup:
    """A setup replayed in isolation from the live trading database."""

    config: dict[str, Any]
    initial_status: SetupStatus | str | None = None
    enabled: bool | None = None


@dataclass(slots=True)
class ExpectedOpportunity:
    """Expected signal used to detect opportunities missed during replay."""

    setup_id: str
    expected_action: SignalAction | str = SignalAction.ENTRY_READY
    from_snapshot_index: int = 0
    by_snapshot_index: int | None = None
    label: str = ""


@dataclass(slots=True)
class ReplayEvaluation:
    setup_id: str
    symbol: str
    setup_type: str
    snapshot_index: int
    status_before: str
    action: str
    reason: str
    status_after: str
    target_status: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    new_stop: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    opportunity_score: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReplayStep:
    snapshot_index: int
    snapshot: MarketSnapshot
    evaluations: list[ReplayEvaluation] = field(default_factory=list)


@dataclass(slots=True)
class MissedOpportunity:
    expected: ExpectedOpportunity
    reason: str
    last_evaluation: ReplayEvaluation | None = None


@dataclass(slots=True)
class OpportunityAuditReport:
    steps: list[ReplayStep]
    expected_opportunities: list[ExpectedOpportunity]
    missed_opportunities: list[MissedOpportunity]
    summary: dict[str, Any]

    @property
    def evaluations(self) -> list[ReplayEvaluation]:
        return [evaluation for step in self.steps for evaluation in step.evaluations]

    @property
    def entry_evaluations(self) -> list[ReplayEvaluation]:
        return [
            evaluation
            for evaluation in self.evaluations
            if evaluation.action == SignalAction.ENTRY_READY.value
        ]

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(
            {
                "summary": self.summary,
                "steps": self.steps,
                "expected_opportunities": self.expected_opportunities,
                "missed_opportunities": self.missed_opportunities,
            }
        )
