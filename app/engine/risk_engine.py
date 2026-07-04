from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from app.models import RiskDecision
from app.setups.setup_roles import setup_is_management_only, setup_role_from_config

TRAILING_STOP_STATUSES = {
    "TRAILING_STOP_LOSS_READY",
    "TRAILING_STOP_LOSS_NOT_READY",
    "TRAILING_STOP_INITIAL_STOP_MISSING",
    "TRAILING_STOP_BROKER_ORDER_NOT_READY",
    "BLOCKED_TRAILING_STOP_NOT_READY",
    "TRAILING_STOP_ACTIVE",
    "TRAILING_STOP_RAISED",
    "STOP_NOT_LOWERED",
    "POSITION_OPEN_TRAILING_STOP_ACTIVE",
    "POSITION_OPEN_TRAILING_STOP_MISSING_CRITICAL",
    "ENTRY_ORDER_WITHOUT_TRAILING_STOP_CRITICAL",
    "LEGACY_STOP_MIGRATED_TO_TRAILING_STOP",
}


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_open_positions: int
    max_position_amount_usd: float
    max_risk_per_trade_usd: float
    max_daily_loss_usd: float
    max_total_exposure_usd: float
    allow_short: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> RiskLimits:
        risk = config.get("risk", {})
        return cls(
            max_open_positions=int(risk.get("max_open_positions", 5)),
            max_position_amount_usd=float(risk.get("max_position_amount_usd", 250)),
            max_risk_per_trade_usd=float(risk.get("max_risk_per_trade_usd", 15)),
            max_daily_loss_usd=float(risk.get("max_daily_loss_usd", 50)),
            max_total_exposure_usd=float(risk.get("max_total_exposure_usd", 1000)),
            allow_short=bool(risk.get("allow_short", False)),
        )


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    @staticmethod
    def worst_case_entry_price(
        setup_config: dict[str, Any],
        trigger_price: float,
    ) -> float:
        entry = setup_config.get("entry", {})
        if not isinstance(entry, dict):
            return trigger_price
        if str(entry.get("order_type", "STP_LMT")) != "STP_LMT":
            return trigger_price
        if entry.get("maximum_limit_price") is not None:
            return float(entry["maximum_limit_price"])
        if entry.get("limit_price") is not None:
            return float(entry["limit_price"])
        return trigger_price + float(entry.get("limit_offset", 0.0) or 0.0)

    def evaluate(
        self,
        setup_config: dict[str, Any],
        entry_price: float,
        stop_loss: float,
        open_positions: int,
        current_exposure_usd: float,
        daily_pnl_usd: float,
    ) -> RiskDecision:
        role = setup_role_from_config(setup_config, infer_position_management=True)
        if setup_is_management_only(role):
            return RiskDecision(
                False,
                "MANAGEMENT_ONLY setup cannot open a new entry order",
            )
        entry = setup_config.get("entry", {})
        if isinstance(entry, dict) and not bool(entry.get("enabled", True)):
            return RiskDecision(False, "Entry orders are disabled for this setup")
        if entry_price <= 0:
            return RiskDecision(False, "Entry price must be positive")
        trailing = setup_config.get("trailing_stop_loss")
        trailing_stop = _trailing_initial_stop(setup_config)
        trailing_ready = _trailing_stop_order_ready(setup_config)
        if trailing_stop is None:
            if isinstance(trailing, dict) and trailing.get("enabled") is True:
                return RiskDecision(False, "BLOCKED_TRAILING_STOP_NOT_READY")
            return RiskDecision(False, "TRAILING_STOP_LOSS_REQUIRED")
        if trailing_stop <= 0:
            return RiskDecision(False, "trailing_stop_loss.initial_stop must be positive")
        if not trailing_ready:
            return RiskDecision(False, "BLOCKED_TRAILING_STOP_NOT_READY")
        stop_loss = trailing_stop
        worst_case_entry_price = self.worst_case_entry_price(setup_config, entry_price)
        if worst_case_entry_price <= 0:
            return RiskDecision(False, "Worst-case entry price must be positive")
        direction = str(setup_config.get("direction", "long")).strip().lower()
        if direction not in {"long", "short"}:
            return RiskDecision(False, "Direction must be long or short")
        if direction == "short" and not self.limits.allow_short:
            return RiskDecision(False, "Short trading is disabled")
        if direction == "long" and stop_loss >= worst_case_entry_price:
            return RiskDecision(
                False,
                "For a long setup, stop loss must be below entry price",
            )
        if direction == "short" and stop_loss <= worst_case_entry_price:
            return RiskDecision(
                False,
                "For a short setup, stop loss must be above entry price",
            )
        if open_positions >= self.limits.max_open_positions:
            return RiskDecision(False, "Maximum number of open positions reached")
        if daily_pnl_usd <= -abs(self.limits.max_daily_loss_usd):
            return RiskDecision(False, "Daily loss limit reached")

        setup_risk = setup_config.get("risk", {})
        max_position = float(
            setup_risk.get(
                "max_position_amount_usd",
                self.limits.max_position_amount_usd,
            )
        )
        max_risk = float(setup_risk.get("max_risk_usd", self.limits.max_risk_per_trade_usd))
        if max_position <= 0 or max_risk <= 0:
            return RiskDecision(False, "Risk budget must be positive")
        remaining_exposure = self.limits.max_total_exposure_usd - current_exposure_usd
        max_position = min(max_position, remaining_exposure)
        if max_position <= 0:
            return RiskDecision(False, "Maximum exposure reached")

        sizing = calculate_position_size(
            direction,
            worst_case_entry_price,
            stop_loss,
            max_risk,
            max_position,
        )
        risk_per_share = float(sizing["risk_per_share"])
        if sizing["status"] == "INVALID_TRAILING_STOP_RISK":
            return RiskDecision(False, "Risk per share must be positive")
        quantity = int(sizing["maximum_quantity"])
        if quantity <= 0:
            return RiskDecision(False, "Calculated quantity is zero")

        position_amount = quantity * worst_case_entry_price
        risk_amount = quantity * risk_per_share
        return RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=quantity,
            entry_price=round(worst_case_entry_price, 4),
            stop_loss=stop_loss,
            position_amount_usd=round(position_amount, 2),
            risk_amount_usd=round(risk_amount, 2),
            trigger_price=entry_price,
        )

    def evaluate_market_data(
        self,
        snapshot: dict[str, Any],
        *,
        max_spread_pct: float | None = None,
        require_order_submission_ready: bool = True,
    ) -> RiskDecision:
        readiness = snapshot.get("market_data_readiness", {})
        if require_order_submission_ready and isinstance(readiness, dict):
            if readiness.get("order_submission_ready") is False:
                missing = readiness.get("missing") or []
                reason = "Market data is not ready for order submission"
                if missing:
                    reason = f"{reason}: {', '.join(map(str, missing))}"
                return RiskDecision(False, reason)
        bid = _number_or_none(snapshot.get("bid"))
        ask = _number_or_none(snapshot.get("ask"))
        if bid is not None and ask is not None and bid > ask:
            return RiskDecision(False, "Bid price cannot be greater than ask price")
        if max_spread_pct is not None and bid is not None and ask is not None:
            mid = (bid + ask) / 2
            if mid <= 0:
                return RiskDecision(False, "Bid/ask midpoint must be positive")
            spread_pct = ((ask - bid) / mid) * 100
            if spread_pct > max_spread_pct:
                return RiskDecision(
                    False,
                    f"Spread {spread_pct:.2f}% exceeds maximum {max_spread_pct:.2f}%",
                )
        return RiskDecision(True, "Market data approved")


def ratchet_trailing_stop(
    *,
    current_stop: float,
    new_calculated_stop: float,
    direction: str = "long",
) -> tuple[float, str]:
    normalized = str(direction or "long").strip().lower()
    if normalized == "short":
        if new_calculated_stop < current_stop:
            return new_calculated_stop, "TRAILING_STOP_LOWERED"
        return current_stop, "STOP_NOT_RAISED_FOR_SHORT"
    if new_calculated_stop > current_stop:
        return new_calculated_stop, "TRAILING_STOP_RAISED"
    return current_stop, "STOP_NOT_LOWERED"


def validate_trailing_stop_required(setup: dict[str, Any]) -> list[str]:
    config = _config_from_setup(setup)
    errors: list[str] = []
    trailing = config.get("trailing_stop_loss")

    if not isinstance(trailing, dict):
        errors.append("TRAILING_STOP_LOSS_SECTION_MISSING")
        return errors

    if trailing.get("enabled") is not True:
        errors.append("TRAILING_STOP_LOSS_REQUIRED")

    if _number_or_none(trailing.get("initial_stop")) is None:
        errors.append("TRAILING_STOP_INITIAL_STOP_REQUIRED_BEFORE_ARMING")

    entry = config.get("entry", {})
    if isinstance(entry, dict) and entry.get("enabled") is True:
        broker_order = trailing.get("broker_order", {})
        if (
            not isinstance(broker_order, dict)
            or broker_order.get("required_before_entry_transmission") is not True
        ):
            errors.append("TRAILING_STOP_BROKER_ORDER_REQUIRED")

    return errors


def can_transmit_entry_order(
    setup: dict[str, Any],
    broker_state: dict[str, Any],
) -> tuple[bool, list[str]]:
    config = _config_from_setup(setup)
    broker_state = broker_state if isinstance(broker_state, dict) else {}
    blocking_reasons: list[str] = []
    trailing = config.get("trailing_stop_loss", {})

    if not isinstance(trailing, dict) or trailing.get("enabled") is not True:
        blocking_reasons.append("TRAILING_STOP_LOSS_NOT_ENABLED")
    else:
        broker_order = trailing.get("broker_order", {})
        if (
            not isinstance(broker_order, dict)
            or broker_order.get("required_before_entry_transmission") is not True
        ):
            blocking_reasons.append("TRAILING_STOP_BROKER_ORDER_REQUIRED")

    if not isinstance(trailing, dict) or _number_or_none(trailing.get("initial_stop")) is None:
        blocking_reasons.append("TRAILING_STOP_INITIAL_STOP_MISSING")

    if broker_state.get("trailing_stop_order_ready") is not True:
        blocking_reasons.append("TRAILING_STOP_BROKER_ORDER_NOT_READY")

    if broker_state.get("broker_tracker_status") != "OK":
        blocking_reasons.append("BROKER_TRACKER_NOT_OK")

    if broker_state.get("tws_connected") is not True:
        blocking_reasons.append("TWS_DISCONNECTED")

    if blocking_reasons:
        return False, blocking_reasons
    return True, []


def calculate_position_size(
    direction: str,
    worst_case_entry_price: float,
    trailing_initial_stop: float,
    max_risk_usd: float,
    max_position_amount_usd: float,
) -> dict[str, Any]:
    if str(direction or "").strip().lower() == "long":
        risk_per_share = worst_case_entry_price - trailing_initial_stop
    else:
        risk_per_share = trailing_initial_stop - worst_case_entry_price

    if risk_per_share <= 0:
        return {
            "maximum_quantity": 0,
            "risk_per_share": risk_per_share,
            "status": "INVALID_TRAILING_STOP_RISK",
        }

    max_qty_by_risk = floor(max_risk_usd / risk_per_share)
    max_qty_by_budget = floor(max_position_amount_usd / worst_case_entry_price)
    maximum_quantity = min(max_qty_by_risk, max_qty_by_budget)

    return {
        "maximum_quantity": maximum_quantity,
        "risk_per_share": round(risk_per_share, 4),
        "max_qty_by_risk": max_qty_by_risk,
        "max_qty_by_budget": max_qty_by_budget,
        "status": "OK" if maximum_quantity > 0 else "QUANTITY_ZERO",
    }


def calculate_initial_trailing_stop_long(
    entry_price: float,
    atr_1h: float,
    support_level: float | None,
    spread: float,
    tick_size: float,
    volatility_regime: str,
) -> dict[str, Any]:
    regime = str(volatility_regime or "").strip().upper()
    if regime == "HIGH":
        atr_multiplier = 2.5
    elif regime == "LOW":
        atr_multiplier = 1.3
    else:
        atr_multiplier = 1.8

    atr_stop = entry_price - (atr_1h * atr_multiplier)
    buffer_value = max(tick_size * 2, spread * 2, atr_1h * 0.1)

    structure_stop = None
    if support_level is not None:
        structure_stop = support_level - buffer_value

    if structure_stop is not None:
        initial_stop = min(atr_stop, structure_stop)
        method_used = "HYBRID_ATR_STRUCTURE"
    else:
        initial_stop = atr_stop
        method_used = "ATR_BASED"

    return {
        "initial_stop": round(initial_stop, 2),
        "method_used": method_used,
        "atr_stop": round(atr_stop, 2),
        "structure_stop": round(structure_stop, 2) if structure_stop is not None else None,
        "atr_multiplier": atr_multiplier,
        "buffer_value": round(buffer_value, 4),
    }


def update_trailing_stop_long(
    current_stop: float,
    new_calculated_stop: float,
) -> dict[str, Any]:
    if new_calculated_stop <= current_stop:
        return {
            "updated": False,
            "final_stop": current_stop,
            "event": "STOP_NOT_LOWERED",
        }

    return {
        "updated": True,
        "final_stop": new_calculated_stop,
        "event": "TRAILING_STOP_RAISED",
    }


def migrate_legacy_stop_to_trailing_stop(setup: dict[str, Any]) -> dict[str, Any]:
    risk = setup.get("risk", {})
    if not isinstance(risk, dict):
        risk = {}

    legacy_stop = risk.get("initial_stop_loss") or risk.get("protective_stop")

    if "trailing_stop_loss" not in setup:
        setup["trailing_stop_loss"] = {
            "enabled": True,
            "mode": "AUTO_INTELLIGENT",
            "never_lower_stop": True,
            "initial_stop": legacy_stop,
            "current_stop": legacy_stop,
            "stop_source": "MIGRATED_FROM_LEGACY_STOP",
            "calculation": {
                "method": "HYBRID_ATR_STRUCTURE",
            },
            "broker_order": {
                "order_type": "TRAIL_OR_MANAGED_STOP",
                "attach_to_entry_order": True,
                "required_before_entry_transmission": True,
                "fallback_to_managed_stop_updates": True,
            },
        }

    risk.pop("initial_stop_loss", None)
    risk.pop("protective_stop", None)
    risk.pop("never_lower_stop", None)
    risk.pop("trailing_stop_loss", None)
    setup["risk"] = risk
    setup["migration_status"] = "LEGACY_STOP_MIGRATED_TO_TRAILING_STOP"
    return setup


def _config_from_setup(setup: dict[str, Any]) -> dict[str, Any]:
    config = setup.get("config")
    if isinstance(config, dict):
        return config
    return setup


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trailing_initial_stop(setup_config: dict[str, Any]) -> float | None:
    trailing = setup_config.get("trailing_stop_loss")
    if isinstance(trailing, dict) and trailing.get("enabled") is True:
        value = _number_or_none(trailing.get("initial_stop"))
        if value is not None:
            return value
    return None


def _trailing_stop_order_ready(setup_config: dict[str, Any]) -> bool:
    trailing = setup_config.get("trailing_stop_loss")
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
