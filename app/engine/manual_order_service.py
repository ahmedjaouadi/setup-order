"""Manual order entry from the UI (etape 11).

A manual order goes through the exact same system gates as a setup order:
trading windows (session policy), trade guards (halt, circuit breakers, PDT,
cooldown, exposure), risk limits, transaction-cost gate, then the order
manager (bracket entry + protective stop for a BUY, reduce-only for a SELL).
There is no bypass path: a BUY without protective stop is refused unless the
broker is the simulated connector AND the caller explicitly asks for it.

Every submission — accepted or refused — leaves a ``decision_traces`` row with
``decision_type="MANUAL_ORDER"`` carrying the full payload and the verdict.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.engine.order_manager import OrderManager
from app.engine.risk_engine import RiskLimits
from app.engine.session_policy import execution_window_block
from app.engine.trade_guards import (
    REASON_EXPOSURE_LIMIT,
    REASON_MISSING_MARKET_DATA,
    REASON_OUTSIDE_TRADING_WINDOW,
    REASON_RISK_TOO_HIGH,
    STATUS_NO_GO,
    STATUS_WAIT,
    TradeGuardsService,
)
from app.engine.transaction_costs import COST_GATE_NO_GO, evaluate_cost_gate
from app.models import EventLevel, OrderStatus, RiskDecision
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id

logger = logging.getLogger(__name__)

REASON_MANUAL_STOP_REQUIRED = "MANUAL_STOP_REQUIRED"
REASON_MANUAL_STOP_INVALID = "MANUAL_STOP_INVALID"
REASON_MANUAL_PRICE_REQUIRED = "MANUAL_PRICE_REQUIRED"
REASON_MANUAL_SELL_EXCEEDS_POSITION = "MANUAL_SELL_EXCEEDS_POSITION"
REASON_BROKER_REJECTED = "BROKER_REJECTED"

_PRICE_CONSUMING_TYPES = {"LMT", "STP", "STP_LMT"}


class ManualOrderService:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        order_manager: OrderManager,
        trade_guards: TradeGuardsService,
        risk_limits: RiskLimits,
        settings: dict[str, Any],
        *,
        broker: Any,
        market_snapshot_provider: Callable[[str], Any | None],
        account_summary_reader: Callable[[], Awaitable[dict[str, Any]]],
        current_time_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.order_manager = order_manager
        self.trade_guards = trade_guards
        self.risk_limits = risk_limits
        self.settings = settings if isinstance(settings, dict) else {}
        self.broker = broker
        self.market_snapshot_provider = market_snapshot_provider
        self.account_summary_reader = account_summary_reader
        self.current_time_provider = current_time_provider or (lambda: datetime.now(UTC))

    # -- public API ---------------------------------------------------------
    async def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Dry-run: same checks as submit, nothing sent, nothing traced."""
        assessment = await self._assess(payload)
        return self._result_payload(assessment)

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        assessment = await self._assess(payload)
        if assessment.get("validation_error") or assessment.get("block"):
            self._trace(assessment, orders=None)
            self.event_store.record(
                EventLevel.RISK,
                "manual_order_rejected",
                self._refusal_message(assessment),
                setup_id=assessment["setup_id"],
                symbol=assessment["symbol"],
                data={
                    "reason_code": self._refusal_reason_code(assessment),
                    "payload": assessment["payload"],
                },
            )
            return self._result_payload(assessment)

        if assessment["side"] == "BUY":
            orders = await self._submit_buy(assessment)
        else:
            orders = await self._submit_sell(assessment)
        self._trace(assessment, orders=orders)
        result = self._result_payload(assessment)
        result.update(orders)
        return result

    # -- assessment ---------------------------------------------------------
    async def _assess(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "").strip().upper()
        side = str(payload.get("side") or "").strip().upper()
        order_type = str(payload.get("order_type") or "").strip().upper()
        quantity = int(payload.get("quantity") or 0)
        limit_price = _number(payload.get("limit_price"))
        trigger_price = _number(payload.get("trigger_price"))
        stop_loss = _number(payload.get("stop_loss"))
        allow_unprotected = bool(payload.get("allow_unprotected"))
        now = self.current_time_provider()

        assessment: dict[str, Any] = {
            "setup_id": new_id("man"),
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "limit_price": limit_price,
            "trigger_price": trigger_price,
            "stop_loss": stop_loss,
            "payload": {**payload, "symbol": symbol, "side": side, "order_type": order_type},
            "validation_error": None,
            "block": None,
            "risk": None,
        }

        validation_error = self._validate_prices(order_type, limit_price, trigger_price)
        if validation_error:
            assessment["validation_error"] = validation_error
            return assessment

        if side == "BUY":
            await self._assess_buy(assessment, allow_unprotected, now)
        else:
            self._assess_sell(assessment, now)
        return assessment

    async def _assess_buy(
        self,
        assessment: dict[str, Any],
        allow_unprotected: bool,
        now: datetime,
    ) -> None:
        symbol = assessment["symbol"]
        stop_loss = assessment["stop_loss"]
        unprotected_allowed = allow_unprotected and self._broker_is_simulated()
        if stop_loss is None and not unprotected_allowed:
            assessment["validation_error"] = {
                "reason_code": REASON_MANUAL_STOP_REQUIRED,
                "message": (
                    "A protective stop (stop_loss) is required for a manual BUY order. "
                    "allow_unprotected is only honored on the simulated broker."
                ),
            }
            return

        reference_entry = self._reference_entry_price(assessment)
        if reference_entry is None:
            assessment["block"] = {
                "status": STATUS_WAIT,
                "reason_code": REASON_MISSING_MARKET_DATA,
                "message": (
                    f"No market price available for {symbol}: cannot size the risk "
                    "of a MKT order."
                ),
                "source": "market_data",
            }
            return
        if stop_loss is not None and stop_loss >= reference_entry:
            assessment["validation_error"] = {
                "reason_code": REASON_MANUAL_STOP_INVALID,
                "message": (
                    f"Protective stop {stop_loss} must be below the worst-case "
                    f"entry price {reference_entry} for a BUY order."
                ),
            }
            return

        risk = await self._risk_summary(assessment, reference_entry)
        assessment["risk"] = risk

        window_block = execution_window_block(self.settings, current_time=now)
        if window_block is not None:
            assessment["block"] = {
                "status": STATUS_WAIT,
                "reason_code": REASON_OUTSIDE_TRADING_WINDOW,
                "message": str(
                    window_block.get("display_message") or "Entry blocked by market hours"
                ),
                "source": "session_policy",
                "context": window_block,
            }
            return

        # This call is the ONLY protection against stacking a manual BUY on a
        # symbol already held: a fresh setup_id is minted per call (see
        # new_id("man") above), so protection_snapshot_for_setup/
        # DuplicateOrderError further down never sees a prior manual order on
        # this symbol. The actual guard is trade_guards._exposure_verdict's
        # block_if_position_on_same_symbol rule, keyed by symbol, not
        # setup_id -- and it depends entirely on the
        # trade_guards.exposure.block_if_position_on_same_symbol config
        # switch staying True (audit 32/S4; locked by
        # tests/test_manual_order_guard_config_lock.py).
        guard_verdict = self.trade_guards.evaluate_entry(symbol, now=now)
        if guard_verdict is not None:
            assessment["block"] = {
                "status": guard_verdict.status,
                "reason_code": guard_verdict.reason_code,
                "message": guard_verdict.message,
                "source": "trade_guards",
                "context": guard_verdict.as_payload(),
            }
            return

        risk_block = self._risk_limits_block(risk)
        if risk_block is not None:
            assessment["block"] = risk_block
            return

        cost_gate = risk.get("cost_gate") or {}
        if cost_gate.get("gate") == COST_GATE_NO_GO:
            assessment["block"] = {
                "status": STATUS_NO_GO,
                "reason_code": REASON_RISK_TOO_HIGH,
                "message": (
                    "Estimated transaction costs are "
                    f"{cost_gate.get('cost_to_risk_ratio'):.0%} of the trade risk "
                    f"(max {cost_gate.get('max_cost_to_risk_ratio'):.0%})."
                ),
                "source": "transaction_costs",
                "context": cost_gate,
            }

    def _assess_sell(self, assessment: dict[str, Any], now: datetime) -> None:
        symbol = assessment["symbol"]
        quantity = assessment["quantity"]
        position = self.repository.get_position(symbol)
        held = int(_number((position or {}).get("quantity"), 0.0) or 0)
        if position is None or held <= 0 or quantity > held:
            assessment["block"] = {
                "status": STATUS_NO_GO,
                "reason_code": REASON_MANUAL_SELL_EXCEEDS_POSITION,
                "message": (
                    f"Manual SELL is reduce-only: requested {quantity}, "
                    f"held {max(held, 0)} on {symbol}."
                ),
                "source": "manual_order",
            }
            return
        # A SELL reduces risk: only the halt and market-closed gates apply
        # (same set as a stop modification).
        verdict = self.trade_guards.evaluate_stop_modification(symbol, now=now)
        if verdict is not None:
            assessment["block"] = {
                "status": verdict.status,
                "reason_code": verdict.reason_code,
                "message": verdict.message,
                "source": "trade_guards",
                "context": verdict.as_payload(),
            }

    # -- submission ---------------------------------------------------------
    async def _submit_buy(self, assessment: dict[str, Any]) -> dict[str, Any]:
        risk = assessment["risk"] or {}
        reference_entry = float(risk["reference_entry_price"])
        stop_loss = assessment["stop_loss"]
        if stop_loss is None:
            # Unprotected BUY: already validated as simulated-only.
            order = await self.order_manager.place_manual_order(
                setup_id=assessment["setup_id"],
                symbol=assessment["symbol"],
                side="BUY",
                quantity=assessment["quantity"],
                order_type=assessment["order_type"],
                limit_price=assessment["limit_price"],
                trigger_price=assessment["trigger_price"],
            )
            if order.status in {OrderStatus.REJECTED.value, OrderStatus.ERROR.value}:
                assessment["block"] = self._broker_rejected_block("entry")
            else:
                self.trade_guards.record_entry_submitted(assessment["symbol"])
            return {"order_id": order.id, "order_status": order.status}
        setup = self._synthetic_setup(assessment)
        decision = RiskDecision(
            approved=True,
            reason="Manual order",
            quantity=assessment["quantity"],
            entry_price=reference_entry,
            stop_loss=float(stop_loss) if stop_loss is not None else 0.0,
            position_amount_usd=float(risk.get("position_amount_usd") or 0.0),
            risk_amount_usd=float(risk.get("risk_usd") or 0.0),
            trigger_price=assessment["trigger_price"],
        )
        order = await self.order_manager.place_entry_order(setup, decision)
        if order.status in {OrderStatus.REJECTED.value, OrderStatus.ERROR.value}:
            assessment["block"] = self._broker_rejected_block("entry")
            return {"order_id": order.id, "order_status": order.status}
        self.trade_guards.record_entry_submitted(assessment["symbol"])
        stop_order_id = next(
            (
                str(row.get("id"))
                for row in self.repository.list_orders(assessment["setup_id"])
                if str(row.get("side") or "").upper() == "SELL"
            ),
            None,
        )
        return {
            "order_id": order.id,
            "order_status": order.status,
            "stop_order_id": stop_order_id,
        }

    async def _submit_sell(self, assessment: dict[str, Any]) -> dict[str, Any]:
        order = await self.order_manager.place_manual_order(
            setup_id=assessment["setup_id"],
            symbol=assessment["symbol"],
            side="SELL",
            quantity=assessment["quantity"],
            order_type=assessment["order_type"],
            limit_price=assessment["limit_price"],
            trigger_price=assessment["trigger_price"],
        )
        if order.status in {OrderStatus.REJECTED.value, OrderStatus.ERROR.value}:
            assessment["block"] = self._broker_rejected_block("exit")
        return {"order_id": order.id, "order_status": order.status}

    @staticmethod
    def _broker_rejected_block(kind: str) -> dict[str, Any]:
        return {
            "status": STATUS_NO_GO,
            "reason_code": REASON_BROKER_REJECTED,
            "message": f"Manual {kind} order rejected by the broker",
            "source": "broker",
        }

    # -- helpers ------------------------------------------------------------
    def _validate_prices(
        self,
        order_type: str,
        limit_price: float | None,
        trigger_price: float | None,
    ) -> dict[str, Any] | None:
        missing: list[str] = []
        if order_type in {"LMT", "STP_LMT"} and limit_price is None:
            missing.append("limit_price")
        if order_type in {"STP", "STP_LMT"} and trigger_price is None:
            missing.append("trigger_price")
        if not missing:
            return None
        return {
            "reason_code": REASON_MANUAL_PRICE_REQUIRED,
            "message": f"{order_type} order requires: {', '.join(missing)}",
        }

    def _reference_entry_price(self, assessment: dict[str, Any]) -> float | None:
        """Worst-case entry price used for the risk computation."""
        order_type = assessment["order_type"]
        if order_type in {"LMT", "STP_LMT"}:
            return assessment["limit_price"]
        if order_type == "STP":
            return assessment["trigger_price"]
        snapshot = self.market_snapshot_provider(assessment["symbol"])
        return _number(getattr(snapshot, "price", None)) if snapshot is not None else None

    async def _risk_summary(
        self,
        assessment: dict[str, Any],
        reference_entry: float,
    ) -> dict[str, Any]:
        quantity = assessment["quantity"]
        stop_loss = assessment["stop_loss"]
        risk_per_share = None if stop_loss is None else round(reference_entry - stop_loss, 4)
        risk_usd = None if risk_per_share is None else round(quantity * risk_per_share, 2)
        position_amount = round(quantity * reference_entry, 2)
        net_liquidation = await self._net_liquidation()
        risk_pct = None
        if risk_usd is not None and net_liquidation and net_liquidation > 0:
            risk_pct = round(risk_usd / net_liquidation * 100, 3)
        snapshot = self.market_snapshot_provider(assessment["symbol"])
        cost_gate = evaluate_cost_gate(
            quantity=quantity,
            spread=_number(getattr(snapshot, "spread", None)) if snapshot is not None else None,
            max_risk_usd=float(risk_usd or 0.0),
            settings=self.settings,
        )
        return {
            "reference_entry_price": reference_entry,
            "risk_per_share": risk_per_share,
            "risk_usd": risk_usd,
            "risk_pct_of_account": risk_pct,
            "position_amount_usd": position_amount,
            "account_net_liquidation": net_liquidation,
            "cost_gate": cost_gate,
        }

    def _risk_limits_block(self, risk: dict[str, Any]) -> dict[str, Any] | None:
        risk_usd = risk.get("risk_usd")
        if risk_usd is not None and risk_usd > self.risk_limits.max_risk_per_trade_usd:
            return {
                "status": STATUS_NO_GO,
                "reason_code": REASON_RISK_TOO_HIGH,
                "message": (
                    f"Trade risk {risk_usd:.2f} USD exceeds the limit of "
                    f"{self.risk_limits.max_risk_per_trade_usd:.2f} USD per trade."
                ),
                "source": "risk_limits",
            }
        position_amount = float(risk.get("position_amount_usd") or 0.0)
        if position_amount > self.risk_limits.max_position_amount_usd:
            return {
                "status": STATUS_NO_GO,
                "reason_code": REASON_RISK_TOO_HIGH,
                "message": (
                    f"Position amount {position_amount:.2f} USD exceeds the limit of "
                    f"{self.risk_limits.max_position_amount_usd:.2f} USD."
                ),
                "source": "risk_limits",
            }
        exposure = sum(
            abs(_number(position.get("average_price"), 0.0) or 0.0)
            * abs(_number(position.get("quantity"), 0.0) or 0.0)
            for position in self.repository.list_positions()
        )
        if exposure + position_amount > self.risk_limits.max_total_exposure_usd:
            return {
                "status": STATUS_NO_GO,
                "reason_code": REASON_EXPOSURE_LIMIT,
                "message": (
                    f"Total exposure {exposure + position_amount:.2f} USD would exceed "
                    f"the limit of {self.risk_limits.max_total_exposure_usd:.2f} USD."
                ),
                "source": "risk_limits",
            }
        return None

    def _synthetic_setup(self, assessment: dict[str, Any]) -> dict[str, Any]:
        stop_loss = assessment["stop_loss"]
        entry: dict[str, Any] = {
            "enabled": True,
            "order_type": assessment["order_type"],
        }
        if assessment["limit_price"] is not None:
            entry["limit_price"] = assessment["limit_price"]
        return {
            "setup_id": assessment["setup_id"],
            "symbol": assessment["symbol"],
            "config": {
                "mode": str(getattr(self.broker, "account_mode", "paper")),
                "direction": "long",
                "enabled": True,
                "entry": entry,
                "trailing_stop_loss": {
                    "enabled": True,
                    "initial_stop": stop_loss,
                    "never_lower_stop": True,
                    "trailing_stop_order_ready": True,
                    "broker_order": {
                        "required_before_entry_transmission": True,
                        "trailing_stop_order_ready": True,
                    },
                },
            },
        }

    async def _net_liquidation(self) -> float | None:
        try:
            account = await self.account_summary_reader()
        except Exception:
            return None
        return _number(account.get("net_liquidation")) if isinstance(account, dict) else None

    def _broker_is_simulated(self) -> bool:
        return str(getattr(self.broker, "connector_name", "")) == "simulated"

    def _refusal_reason_code(self, assessment: dict[str, Any]) -> str:
        refusal = assessment.get("validation_error") or assessment.get("block") or {}
        return str(refusal.get("reason_code") or "UNKNOWN")

    def _refusal_message(self, assessment: dict[str, Any]) -> str:
        refusal = assessment.get("validation_error") or assessment.get("block") or {}
        return str(refusal.get("message") or "Manual order refused")

    def _trace(self, assessment: dict[str, Any], orders: dict[str, Any] | None) -> None:
        refused = bool(assessment.get("validation_error") or assessment.get("block"))
        if refused:
            block = assessment.get("block") or {}
            status = str(block.get("status") or STATUS_NO_GO)
            final_decision = f"{status}:{self._refusal_reason_code(assessment)}"
        else:
            final_decision = "GO:MANUAL_ORDER_SUBMITTED"
        self.event_store.record_decision_trace(
            decision_type="MANUAL_ORDER",
            final_decision=final_decision,
            symbol=assessment["symbol"],
            setup_id=assessment["setup_id"],
            trace={
                "payload": assessment["payload"],
                "risk": assessment.get("risk"),
                "validation_error": assessment.get("validation_error"),
                "trade_guards": (assessment.get("block") or {}).get("context"),
                "block": assessment.get("block"),
                "orders": orders,
            },
        )

    @staticmethod
    def _result_payload(assessment: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": not (assessment.get("validation_error") or assessment.get("block")),
            "setup_id": assessment["setup_id"],
            "symbol": assessment["symbol"],
            "side": assessment["side"],
            "quantity": assessment["quantity"],
            "order_type": assessment["order_type"],
            "validation_error": assessment.get("validation_error"),
            "block": assessment.get("block"),
            "risk": assessment.get("risk"),
        }


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
