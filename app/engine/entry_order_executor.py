from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.engine.broker_reality import broker_reality_blocking_reasons
from app.engine.order_manager import (
    BrokerModeMismatchError,
    DuplicateOrderError,
    ManagementOnlyEntryError,
    OrderManager,
    UnprotectedActiveOrderError,
)
from app.engine.risk_engine import RiskEngine
from app.engine.session_policy import execution_window_block, signal_blocked_by_session_policy
from app.engine.trade_guards import TradeGuardsService
from app.engine.transaction_costs import COST_GATE_NO_GO, COST_GATE_WARNING, evaluate_cost_gate
from app.models import EventLevel, SetupStatus, SignalAction
from app.setups.setup_roles import setup_is_management_only, setup_role_from_config
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class EntryOrderExecutor:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        risk_engine: RiskEngine,
        order_manager: OrderManager,
        settings: dict[str, Any] | None = None,
        current_time_provider: Callable[[], datetime] | None = None,
        lifecycle_service: Any | None = None,
        trade_guards: TradeGuardsService | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.risk_engine = risk_engine
        self.order_manager = order_manager
        self.settings = settings if isinstance(settings, dict) else {}
        self.current_time_provider = current_time_provider or (lambda: datetime.now(UTC))
        self.lifecycle_service = lifecycle_service
        self.trade_guards = trade_guards

    async def execute_entry_ready(
        self,
        setup: dict[str, Any],
        signal: Any,
    ) -> bool:
        if signal.action != SignalAction.ENTRY_READY:
            return False

        if signal_blocked_by_session_policy(signal):
            analysis = (
                signal.metadata.get("analysis", {}) if isinstance(signal.metadata, dict) else {}
            )
            self.event_store.record(
                EventLevel.WARNING,
                "entry_blocked_by_session_policy",
                str(
                    analysis.get("display_message")
                    or signal.reason
                    or "Entry blocked by session policy"
                ),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data={
                    "decision_status": analysis.get("decision_status"),
                    "next_action": analysis.get("next_action"),
                    "blocking_conditions": analysis.get("blocking_conditions"),
                },
            )
            return True

        runtime_window_block = execution_window_block(
            self.settings,
            current_time=self.current_time_provider(),
        )
        if runtime_window_block is not None:
            self.event_store.record(
                EventLevel.WARNING,
                "entry_blocked_by_execution_session_window",
                str(runtime_window_block.get("display_message") or "Entry blocked by market hours"),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data=runtime_window_block,
            )
            return True

        if self.trade_guards is not None:
            guard_verdict = self.trade_guards.evaluate_entry(
                setup["symbol"],
                setup=setup,
                now=self.current_time_provider(),
            )
            if guard_verdict is not None:
                self.event_store.record(
                    EventLevel.RISK,
                    "entry_blocked_by_trade_guards",
                    f"{guard_verdict.decision_status}: {guard_verdict.message}",
                    setup_id=setup["setup_id"],
                    symbol=setup["symbol"],
                    data=guard_verdict.as_payload(),
                )
                return True

        setup_role = setup_role_from_config(setup.get("config", {}))
        if setup_is_management_only(setup_role):
            self.event_store.record(
                EventLevel.CRITICAL,
                "management_only_entry_blocked",
                "MANAGEMENT_ONLY setup cannot place an entry order",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                "MANAGEMENT_ONLY entry signal blocked",
            )
            return True

        if not auto_execution_enabled(setup):
            self.event_store.record(
                EventLevel.WARNING,
                "entry_auto_execution_disabled",
                "Entry signal detected, but automatic TWS execution is OFF",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data={
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "enabled": bool(setup.get("enabled")),
                    "config_enabled": (
                        setup.get("config", {}).get("enabled", True)
                        if isinstance(setup.get("config"), dict)
                        else True
                    ),
                },
            )
            return True

        effective_setup = self._setup_with_signal_overrides(setup, signal)
        if not self._lifecycle_allows_transmission(setup, effective_setup):
            return True
        trailing_stop = _trailing_initial_stop(effective_setup.get("config", {}))
        trailing_ready = _trailing_stop_order_ready(effective_setup.get("config", {}))
        if signal.entry_price is None or trailing_stop is None:
            self.event_store.record(
                EventLevel.ERROR,
                "entry_signal_rejected",
                "Entry signal missing price or trailing stop",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data={
                    "entry_decision": {
                        "status": "BLOCKED_TRAILING_STOP_NOT_READY",
                        "decision": "NO_ENTRY",
                        "can_send_order": False,
                        "blocking_reasons": ["TRAILING_STOP_LOSS_NOT_READY"],
                    }
                },
            )
            return True
        if not trailing_ready:
            self.event_store.record(
                EventLevel.CRITICAL,
                "entry_blocked_trailing_stop_not_ready",
                "Entry blocked because trailing stop-loss is not ready",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data={
                    "entry_decision": {
                        "status": "BLOCKED_TRAILING_STOP_NOT_READY",
                        "decision": "NO_ENTRY",
                        "can_send_order": False,
                        "blocking_reasons": ["TRAILING_STOP_LOSS_NOT_READY"],
                    }
                },
            )
            return True

        positions = self.repository.list_positions()
        open_positions = len(positions)
        exposure = sum(
            float(position["average_price"]) * int(position["quantity"]) for position in positions
        )
        daily_pnl = sum(float(position["unrealized_pnl"]) for position in positions)
        decision = self.risk_engine.evaluate(
            setup_config=effective_setup["config"],
            entry_price=signal.entry_price,
            stop_loss=trailing_stop,
            open_positions=open_positions,
            current_exposure_usd=exposure,
            daily_pnl_usd=daily_pnl,
        )
        if not decision.approved:
            self.event_store.record(
                EventLevel.RISK,
                "entry_rejected_by_risk",
                decision.reason,
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
            return True

        cost_gate = evaluate_cost_gate(
            quantity=int(decision.quantity or 0),
            spread=_spread_hint(signal),
            max_risk_usd=float(decision.risk_amount_usd or 0.0),
            settings=self.settings,
        )
        if cost_gate["gate"] == COST_GATE_NO_GO:
            self.event_store.record(
                EventLevel.RISK,
                "entry_rejected_by_transaction_costs",
                (
                    "Estimated costs are "
                    f"{cost_gate['cost_to_risk_ratio']:.0%} of the trade risk "
                    f"(max {cost_gate['max_cost_to_risk_ratio']:.0%})"
                ),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data=cost_gate,
            )
            return True
        if cost_gate["gate"] == COST_GATE_WARNING:
            self.event_store.record(
                EventLevel.WARNING,
                "entry_transaction_costs_warning",
                ("Estimated costs are " f"{cost_gate['cost_to_risk_ratio']:.0%} of the trade risk"),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data=cost_gate,
            )

        broker_blocking_reasons = broker_reality_blocking_reasons(
            self.repository,
            self.settings,
        )
        if broker_blocking_reasons:
            self.event_store.record(
                EventLevel.CRITICAL,
                "entry_blocked_by_broker_reality",
                "Automatic entry blocked because broker reality is not safe",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data={"blocking_reasons": broker_blocking_reasons},
            )
            return True

        try:
            await self.order_manager.place_entry_order(effective_setup, decision)
        except BrokerModeMismatchError as exc:
            self.event_store.record(
                EventLevel.RISK,
                "broker_mode_mismatch",
                str(exc),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
        except ManagementOnlyEntryError as exc:
            self.event_store.record(
                EventLevel.CRITICAL,
                "management_only_entry_blocked",
                str(exc),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                str(exc),
            )
        except DuplicateOrderError as exc:
            self.event_store.record(
                EventLevel.RISK,
                "duplicate_order_blocked",
                str(exc),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
        except UnprotectedActiveOrderError as exc:
            self.event_store.record(
                EventLevel.CRITICAL,
                "active_entry_order_unprotected",
                str(exc),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
        else:
            if self.trade_guards is not None:
                # Feed the daily circuit breakers (skills.md 34.3).
                self.trade_guards.record_entry_submitted(setup["symbol"])
        return True

    def _lifecycle_allows_transmission(
        self,
        setup: dict[str, Any],
        effective_setup: dict[str, Any],
    ) -> bool:
        if self.lifecycle_service is None:
            return True
        try:
            result = self.lifecycle_service.revalidate(effective_setup)
        except Exception:
            return True
        status = str(result.get("status") or "")
        blocked_statuses = {
            SetupStatus.INVALIDATED.value,
            SetupStatus.EXPIRED.value,
            SetupStatus.STALE_SETUP.value,
            SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
            SetupStatus.BLOCKED.value,
        }
        if status not in blocked_statuses:
            return True
        # Persist the demotion on the raw setup so the GUI reflects it.
        try:
            self.lifecycle_service.revalidate_and_apply(setup)
        except Exception:
            pass
        self.event_store.record(
            EventLevel.CRITICAL,
            "entry_blocked_by_lifecycle_revalidation",
            f"Entry transmission blocked by setup revalidation: {status}"
            f" ({result.get('status_reason')})",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={
                "entry_decision": {
                    "status": status,
                    "decision": "NO_ENTRY",
                    "can_send_order": False,
                    "blocking_reasons": result.get("blocking_reasons", []),
                },
                "lifecycle": result,
            },
        )
        return False

    @staticmethod
    def _setup_with_signal_overrides(
        setup: dict[str, Any],
        signal: Any,
    ) -> dict[str, Any]:
        metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
        entry_overrides = metadata.get("entry_overrides")
        risk_overrides = metadata.get("risk_overrides")
        trailing_stop_overrides = metadata.get("trailing_stop_overrides")
        if (
            not isinstance(entry_overrides, dict)
            and not isinstance(risk_overrides, dict)
            and not isinstance(trailing_stop_overrides, dict)
        ):
            return setup
        effective = deepcopy(setup)
        config = effective.setdefault("config", {})
        if isinstance(entry_overrides, dict):
            entry = config.setdefault("entry", {})
            for key, value in entry_overrides.items():
                if value is not None:
                    entry[key] = value
        if isinstance(risk_overrides, dict):
            risk = config.setdefault("risk", {})
            for key, value in risk_overrides.items():
                if value is not None:
                    risk[key] = value
        if isinstance(trailing_stop_overrides, dict):
            trailing = config.setdefault("trailing_stop_loss", {})
            if isinstance(trailing, dict):
                trailing["enabled"] = True
                trailing.setdefault("mode", "AUTO_INTELLIGENT")
                trailing.setdefault("never_lower_stop", True)
                for key, value in trailing_stop_overrides.items():
                    if value is not None:
                        trailing[key] = value
        return effective


def auto_execution_enabled(setup: dict[str, Any]) -> bool:
    config = setup.get("config", {})
    config_enabled = config.get("enabled", True) if isinstance(config, dict) else True
    return bool(setup.get("enabled")) and config_enabled is not False


def _trailing_initial_stop(config: dict[str, Any]) -> float | None:
    trailing = config.get("trailing_stop_loss")
    if isinstance(trailing, dict) and trailing.get("enabled") is True:
        value = _number_or_none(trailing.get("initial_stop"))
        if value is not None:
            return value
    return None


def _trailing_stop_order_ready(config: dict[str, Any]) -> bool:
    trailing = config.get("trailing_stop_loss")
    if not isinstance(trailing, dict):
        return False
    if trailing.get("enabled") is not True:
        return False
    broker_order = trailing.get("broker_order")
    if not isinstance(broker_order, dict):
        return False
    if broker_order.get("required_before_entry_transmission") is not True:
        return False
    ready = trailing.get("trailing_stop_order_ready")
    if ready is None:
        ready = broker_order.get("trailing_stop_order_ready")
    return ready is True


def _spread_hint(signal: Any) -> float | None:
    """Best-effort spread extracted from the signal analysis metadata."""
    metadata = getattr(signal, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    analysis = metadata.get("analysis")
    if not isinstance(analysis, dict):
        return None
    costs = analysis.get("transaction_costs")
    if isinstance(costs, dict):
        value = _number_or_none(costs.get("spread_used"))
        if value is not None:
            return value
    return _number_or_none(analysis.get("spread"))


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
