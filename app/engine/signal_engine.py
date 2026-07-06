from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import floor
from typing import Any

from app.engine.entry_decision import attach_entry_decision
from app.engine.session_policy import apply_entry_session_policy
from app.engine.setup_lifecycle_service import LIFECYCLE_MANAGED_STATUSES
from app.engine.trade_guards import (
    REASON_RISK_TOO_HIGH,
    STATUS_NO_GO,
    GuardVerdict,
    TradeGuardsService,
    blocked_signal_from_verdict,
)
from app.engine.transaction_costs import COST_GATE_NO_GO, evaluate_cost_gate
from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction, to_jsonable
from app.settings import DEFAULT_CONFIG
from app.setups.setup_factory import SetupFactory
from app.storage.repositories import TradingRepository

TERMINAL_SIGNAL_STATUSES = {
    SetupStatus.CLOSED,
    SetupStatus.CANCELLED,
    SetupStatus.EXPIRED,
    SetupStatus.INVALIDATED,
    SetupStatus.DISABLED,
    SetupStatus.ERROR,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
    # Lifecycle statuses: the lifecycle revalidation owns these and decides
    # when the setup becomes WAITING_ACTIVATION again.
    SetupStatus.BLOCKED,
    SetupStatus.STALE_SETUP,
    SetupStatus.MISSED_BREAKOUT_WAIT_RETEST,
}

TraceBuilder = Callable[[dict[str, Any], MarketSnapshot, SetupStatus, SetupSignal], dict[str, Any]]


@dataclass(slots=True)
class SignalEvaluation:
    setup: dict[str, Any]
    current_status: SetupStatus
    signal: SetupSignal
    processed: dict[str, Any]


class SignalEngine:
    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
        lifecycle_service: Any | None = None,
        trade_guards: TradeGuardsService | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings if isinstance(settings, dict) else DEFAULT_CONFIG
        self.lifecycle_service = lifecycle_service
        self.trade_guards = trade_guards

    def evaluate_snapshot(
        self,
        snapshot: MarketSnapshot,
        trace_builder: TraceBuilder,
    ) -> list[SignalEvaluation]:
        symbol = snapshot.symbol.upper()
        evaluations: list[SignalEvaluation] = []
        for setup in self.repository.list_setups():
            if setup["symbol"] != symbol:
                continue
            setup = self._revalidate_lifecycle(setup, snapshot)
            current_status = SetupStatus(setup["status"])

            strategy = SetupFactory.create(setup["config"])
            if current_status == SetupStatus.DISABLED:
                current_status = strategy.initial_status()
            if current_status in TERMINAL_SIGNAL_STATUSES:
                continue
            signal = strategy.evaluate(snapshot, current_status)
            signal = apply_entry_session_policy(signal, snapshot, self.settings)
            signal = self._apply_trade_guard_gates(setup, signal, snapshot)
            metadata = attach_entry_decision(
                setup=setup,
                current_status=current_status,
                signal=signal,
                metadata=to_jsonable(signal.metadata),
            )
            signal.metadata = metadata
            self._apply_runtime_entry_guards(setup, signal)
            trace = trace_builder(setup, snapshot, current_status, signal)
            evaluations.append(
                SignalEvaluation(
                    setup=setup,
                    current_status=current_status,
                    signal=signal,
                    processed={
                        "setup_id": setup["setup_id"],
                        "setup_type": setup["setup_type"],
                        "status": current_status.value,
                        "action": signal.action.value,
                        "reason": signal.reason,
                        "target_status": (
                            signal.target_status.value if signal.target_status else None
                        ),
                        "entry_price": signal.entry_price,
                        "stop_loss": signal.stop_loss,
                        "new_stop": signal.new_stop,
                        "metadata": to_jsonable(signal.metadata),
                        "trace": trace,
                    },
                )
            )
        return evaluations

    def _revalidate_lifecycle(
        self,
        setup: dict[str, Any],
        snapshot: MarketSnapshot,
    ) -> dict[str, Any]:
        if self.lifecycle_service is None:
            return setup
        if str(setup.get("status") or "") not in LIFECYCLE_MANAGED_STATUSES:
            return setup
        result = self.lifecycle_service.revalidate_and_apply(
            setup,
            market_snapshot=snapshot,
        )
        return {
            **setup,
            "status": result.get("status", setup.get("status")),
            "status_reason": result.get("status_reason", ""),
            "last_revalidated_at": result.get("last_revalidated_at"),
        }

    def _apply_trade_guard_gates(
        self,
        setup: dict[str, Any],
        signal: SetupSignal,
        snapshot: MarketSnapshot,
    ) -> SetupSignal:
        """System gates from docs/skills.md section 29, applied before the
        setup-level entry decision (system gates come first)."""
        if signal.action != SignalAction.ENTRY_READY:
            return signal
        if self.trade_guards is not None:
            verdict = self.trade_guards.evaluate_entry(snapshot.symbol, setup=setup)
            if verdict is not None:
                return blocked_signal_from_verdict(signal, verdict)
        cost_verdict = self._cost_gate_verdict(setup, signal, snapshot)
        if cost_verdict is not None:
            return blocked_signal_from_verdict(signal, cost_verdict)
        return signal

    def _cost_gate_verdict(
        self,
        setup: dict[str, Any],
        signal: SetupSignal,
        snapshot: MarketSnapshot,
    ) -> GuardVerdict | None:
        """Transaction-cost gate (docs/skills.md section 24bis)."""
        entry_price = _number_or_none(signal.entry_price)
        stop_loss = _number_or_none(signal.stop_loss)
        if entry_price is None or stop_loss is None:
            return None
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0:
            return None
        config_raw = setup.get("config")
        config: dict[str, Any] = config_raw if isinstance(config_raw, dict) else {}
        risk_raw = config.get("risk")
        setup_risk: dict[str, Any] = risk_raw if isinstance(risk_raw, dict) else {}
        default_risk = (
            self.settings.get("risk", {}).get("max_risk_per_trade_usd", 15)
            if isinstance(self.settings.get("risk"), dict)
            else 15
        )
        max_risk = _number_or_none(setup_risk.get("max_risk_usd")) or _number_or_none(default_risk)
        if max_risk is None or max_risk <= 0:
            return None
        quantity = floor(max_risk / risk_per_share)
        if quantity <= 0:
            return None
        gate = evaluate_cost_gate(
            quantity=quantity,
            spread=snapshot.spread,
            max_risk_usd=max_risk,
            settings=self.settings,
        )
        metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
        analysis = metadata.get("analysis")
        if isinstance(analysis, dict):
            analysis["transaction_costs"] = gate
        if gate["gate"] != COST_GATE_NO_GO:
            return None
        return GuardVerdict(
            status=STATUS_NO_GO,
            reason_code=REASON_RISK_TOO_HIGH,
            decision_status="COST_TO_RISK_TOO_HIGH",
            title="Entree refusee - couts trop eleves",
            message=(
                "Les couts estimes (commissions, slippage, spread) representent "
                f"{gate['cost_to_risk_ratio']:.0%} du risque du trade "
                f"(max {gate['max_cost_to_risk_ratio']:.0%})."
            ),
            context={"transaction_costs": gate},
        )

    def _apply_runtime_entry_guards(self, setup: dict[str, Any], signal: SetupSignal) -> None:
        metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
        decision = metadata.get("entry_decision")
        if not isinstance(decision, dict):
            return
        setup_id = str(setup.get("setup_id") or "")
        protection = self.repository.protection_snapshot_for_setup(setup_id)
        if protection.get("position_open") and not protection.get("has_active_stop_order"):
            self._override_entry_decision(
                metadata,
                status="POSITION_OPEN_STOP_MISSING_CRITICAL",
                display_title="Position ouverte sans stop actif",
                display_message="Une position ouverte existe sans stop-loss broker actif.",
                blocking_reason="POSITION_OPEN_WITHOUT_PROTECTIVE_STOP",
                next_action="SUBMIT_PROTECTIVE_STOP_OR_MANUAL_REVIEW",
                protection_status=str(
                    protection.get("protection_status") or "POSITION_OPEN_STOP_MISSING_CRITICAL"
                ),
                protective_stop_order_id=protection.get("active_stop_order_id"),
                protective_stop_order_ready=False,
            )
            return
        if protection.get("active_entry_order_id"):
            if protection.get("has_active_stop_order"):
                self._override_entry_decision(
                    metadata,
                    status="DUPLICATE_ORDER_BLOCKED",
                    display_title="Ordre deja actif",
                    display_message="Un ordre actif protege existe deja pour ce setup.",
                    blocking_reason="DUPLICATE_ORDER_BLOCKED",
                    next_action="WAIT_EXISTING_PROTECTED_ORDER",
                    protection_status=str(
                        protection.get("protection_status") or "BRACKET_ORDER_SUBMITTED"
                    ),
                    protective_stop_order_id=protection.get("active_stop_order_id"),
                    protective_stop_order_ready=True,
                )
                return
            self._override_entry_decision(
                metadata,
                status="ACTIVE_ENTRY_ORDER_UNPROTECTED",
                display_title="Ordre actif non protege",
                display_message="Un ordre d'entree existe deja mais aucun stop-loss protecteur attache n'a ete trouve.",
                blocking_reason="ACTIVE_ORDER_WITHOUT_PROTECTIVE_STOP",
                next_action="CANCEL_OR_ATTACH_STOP_MANUAL_REVIEW",
                protection_status=str(
                    protection.get("protection_status")
                    or "ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED"
                ),
                protective_stop_order_id=None,
                protective_stop_order_ready=False,
            )

    @staticmethod
    def _override_entry_decision(
        metadata: dict[str, Any],
        *,
        status: str,
        display_title: str,
        display_message: str,
        blocking_reason: str,
        next_action: str,
        protection_status: str,
        protective_stop_order_id: Any,
        protective_stop_order_ready: bool,
    ) -> None:
        decision = metadata.get("entry_decision")
        if not isinstance(decision, dict):
            return
        blocking = decision.get("blocking_reasons")
        if not isinstance(blocking, list):
            blocking = []
        blocking = [str(item) for item in blocking if item not in (None, "")]
        if blocking_reason not in blocking:
            blocking.append(blocking_reason)
        decision.update(
            {
                "status": status,
                "decision": "NO_ENTRY",
                "can_send_order": False,
                "display_title": display_title,
                "display_message": display_message,
                "next_action": next_action,
                "blocking_reasons": blocking,
                "protection_status": protection_status,
                "protective_stop_order_id": protective_stop_order_id,
                "protective_stop_order_ready": protective_stop_order_ready,
            }
        )
        analysis = metadata.get("analysis")
        if not isinstance(analysis, dict):
            analysis = {}
            metadata["analysis"] = analysis
        analysis["decision_status"] = status
        analysis["decision"] = "NO_ENTRY"
        analysis["display_title"] = display_title
        analysis["display_message"] = display_message
        analysis["next_action"] = next_action
        analysis["blocking_conditions"] = blocking
        analysis["entry_decision"] = decision


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
