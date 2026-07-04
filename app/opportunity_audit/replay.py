from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from app.engine.opportunity_alert_service import score_processed_item
from app.engine.session_policy import apply_entry_session_policy
from app.engine.setup_diagnostics import build_setup_analysis_trace
from app.engine.signal_engine import TERMINAL_SIGNAL_STATUSES
from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction, to_jsonable
from app.opportunity_audit.models import (
    ExpectedOpportunity,
    MissedOpportunity,
    OpportunityAuditReport,
    ReplayEvaluation,
    ReplaySetup,
    ReplayStep,
)
from app.settings import DEFAULT_CONFIG
from app.setups.setup_factory import SetupFactory

TraceBuilder = Callable[[dict[str, Any], MarketSnapshot, SetupStatus, SetupSignal], dict[str, Any]]


class OpportunityReplayEngine:
    """Replay market snapshots against setup rules without touching live state."""

    def __init__(
        self,
        trace_builder: TraceBuilder = build_setup_analysis_trace,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.trace_builder = trace_builder
        self.settings = settings if isinstance(settings, dict) else DEFAULT_CONFIG

    def run(
        self,
        setups: Iterable[ReplaySetup | dict[str, Any]],
        snapshots: Iterable[MarketSnapshot],
        expected_opportunities: Iterable[ExpectedOpportunity] | None = None,
        evolve_status: bool = True,
    ) -> OpportunityAuditReport:
        replay_setups = [normalize_replay_setup(setup) for setup in setups]
        snapshot_list = list(snapshots)
        statuses = {
            setup_id_for(replay_setup.config): initial_status_for(replay_setup)
            for replay_setup in replay_setups
        }

        steps: list[ReplayStep] = []
        for snapshot_index, snapshot in enumerate(snapshot_list):
            evaluations: list[ReplayEvaluation] = []
            symbol = snapshot.symbol.upper()
            for replay_setup in replay_setups:
                strategy = SetupFactory.create(replay_setup.config)
                setup_id = strategy.setup_id
                enabled = replay_setup.enabled
                if enabled is None:
                    enabled = strategy.enabled
                if strategy.symbol != symbol:
                    continue

                status_before = statuses[setup_id]
                if status_before == SetupStatus.DISABLED:
                    status_before = strategy.initial_status()
                    statuses[setup_id] = status_before
                if status_before in TERMINAL_SIGNAL_STATUSES:
                    continue
                setup_payload = setup_payload_for(
                    replay_setup,
                    status_before,
                    enabled=enabled,
                )
                signal = strategy.evaluate(snapshot, status_before)
                signal = apply_entry_session_policy(signal, snapshot, self.settings)
                status_after = status_before
                if evolve_status and signal.target_status is not None:
                    status_after = signal.target_status
                    statuses[setup_id] = status_after

                evaluations.append(
                    replay_evaluation_for(
                        setup_payload=setup_payload,
                        snapshot=snapshot,
                        snapshot_index=snapshot_index,
                        status_before=status_before,
                        status_after=status_after,
                        signal=signal,
                        trace_builder=self.trace_builder,
                    )
                )
            steps.append(
                ReplayStep(
                    snapshot_index=snapshot_index,
                    snapshot=snapshot,
                    evaluations=evaluations,
                )
            )

        expected = list(expected_opportunities or [])
        missed = missed_opportunities_for(steps, expected)
        return OpportunityAuditReport(
            steps=steps,
            expected_opportunities=expected,
            missed_opportunities=missed,
            summary=summary_for(
                steps=steps,
                expected_opportunities=expected,
                missed_opportunities=missed,
            ),
        )


def normalize_replay_setup(setup: ReplaySetup | dict[str, Any]) -> ReplaySetup:
    if isinstance(setup, ReplaySetup):
        return setup
    return ReplaySetup(config=setup)


def initial_status_for(setup: ReplaySetup) -> SetupStatus:
    if setup.initial_status is not None:
        return SetupStatus(setup.initial_status)
    return SetupFactory.create(setup.config).initial_status()


def setup_id_for(config: dict[str, Any]) -> str:
    return SetupFactory.create(config).setup_id


def setup_payload_for(
    setup: ReplaySetup,
    status: SetupStatus,
    enabled: bool,
) -> dict[str, Any]:
    strategy = SetupFactory.create(setup.config)
    return {
        "setup_id": strategy.setup_id,
        "symbol": strategy.symbol,
        "setup_type": strategy.setup_type,
        "enabled": enabled,
        "mode": strategy.mode,
        "status": status.value,
        "config": setup.config,
    }


def replay_evaluation_for(
    setup_payload: dict[str, Any],
    snapshot: MarketSnapshot,
    snapshot_index: int,
    status_before: SetupStatus,
    status_after: SetupStatus,
    signal: SetupSignal,
    trace_builder: TraceBuilder,
) -> ReplayEvaluation:
    trace = trace_builder(setup_payload, snapshot, status_before, signal)
    processed = {
        "setup_id": setup_payload["setup_id"],
        "setup_type": setup_payload["setup_type"],
        "status": status_before.value,
        "action": signal.action.value,
        "reason": signal.reason,
        "metadata": to_jsonable(signal.metadata),
        "trace": trace,
    }
    return ReplayEvaluation(
        setup_id=str(setup_payload["setup_id"]),
        symbol=str(setup_payload["symbol"]),
        setup_type=str(setup_payload["setup_type"]),
        snapshot_index=snapshot_index,
        status_before=status_before.value,
        action=signal.action.value,
        reason=signal.reason,
        status_after=status_after.value,
        target_status=signal.target_status.value if signal.target_status else None,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        new_stop=signal.new_stop,
        metadata=to_jsonable(signal.metadata),
        trace=trace,
        opportunity_score=score_processed_item(processed),
    )


def missed_opportunities_for(
    steps: list[ReplayStep],
    expected_opportunities: list[ExpectedOpportunity],
) -> list[MissedOpportunity]:
    missed: list[MissedOpportunity] = []
    evaluations = [evaluation for step in steps for evaluation in step.evaluations]
    for expected in expected_opportunities:
        expected_action = SignalAction(expected.expected_action).value
        window_end = (
            expected.by_snapshot_index
            if expected.by_snapshot_index is not None
            else max((step.snapshot_index for step in steps), default=-1)
        )
        matching = [
            evaluation
            for evaluation in evaluations
            if evaluation.setup_id == expected.setup_id
            and expected.from_snapshot_index <= evaluation.snapshot_index <= window_end
        ]
        if any(evaluation.action == expected_action for evaluation in matching):
            continue
        last_evaluation = matching[-1] if matching else None
        missed.append(
            MissedOpportunity(
                expected=expected,
                reason=missed_reason(expected_action, last_evaluation),
                last_evaluation=last_evaluation,
            )
        )
    return missed


def missed_reason(
    expected_action: str,
    last_evaluation: ReplayEvaluation | None,
) -> str:
    if last_evaluation is None:
        return f"No evaluation was produced before expected {expected_action}"
    return (
        f"Expected {expected_action} but last signal was "
        f"{last_evaluation.action}: {last_evaluation.reason}"
    )


def summary_for(
    steps: list[ReplayStep],
    expected_opportunities: list[ExpectedOpportunity],
    missed_opportunities: list[MissedOpportunity],
) -> dict[str, Any]:
    evaluations = [evaluation for step in steps for evaluation in step.evaluations]
    action_counts = Counter(evaluation.action for evaluation in evaluations)
    setup_ids = sorted({evaluation.setup_id for evaluation in evaluations})
    return {
        "snapshots_replayed": len(steps),
        "evaluations": len(evaluations),
        "setups_evaluated": len(setup_ids),
        "setup_ids": setup_ids,
        "actions": dict(sorted(action_counts.items())),
        "entries_detected": action_counts.get(SignalAction.ENTRY_READY.value, 0),
        "expected_opportunities": len(expected_opportunities),
        "missed_opportunities": len(missed_opportunities),
    }
