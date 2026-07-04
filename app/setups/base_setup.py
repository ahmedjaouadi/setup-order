from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

from app.models import (
    MarketSnapshot,
    SetupRecord,
    SetupRole,
    SetupSignal,
    SetupStatus,
    ValidationResult,
)
from app.setups.setup_roles import (
    entry_policy_errors,
    is_valid_setup_role,
    setup_allows_entry,
    setup_is_management_only,
    setup_role_from_config,
)


class BaseSetup(ABC):
    setup_type: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = _with_legacy_trailing_stop(config)

    @property
    def setup_id(self) -> str:
        return str(self.config.get("setup_id", "")).strip()

    @property
    def symbol(self) -> str:
        return str(self.config.get("symbol", "")).strip().upper()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def mode(self) -> str:
        return str(self.config.get("mode", "paper"))

    @property
    def setup_role(self) -> SetupRole:
        return setup_role_from_config(self.config)

    @property
    def allows_entry(self) -> bool:
        return setup_allows_entry(self.setup_role)

    @property
    def stop_loss(self) -> float | None:
        trailing = self.config.get("trailing_stop_loss", {})
        stop = trailing.get("initial_stop") if isinstance(trailing, dict) else None
        return float(stop) if stop is not None else None

    def initial_status(self) -> SetupStatus:
        if setup_is_management_only(self.setup_role):
            return SetupStatus.RECONCILING_EXISTING_POSITION
        return SetupStatus.WAITING_ACTIVATION

    def validate(self) -> ValidationResult:
        errors: list[str] = []
        if not self.setup_id:
            errors.append("setup_id is required")
        if not self.symbol:
            errors.append("symbol is required")
        if self.config.get("setup_type") != self.setup_type:
            errors.append(f"setup_type must be {self.setup_type}")
        if self.mode not in {"paper", "live"}:
            errors.append("mode must be paper or live")
        if not is_valid_setup_role(self.config.get("setup_role", self.setup_role.value)):
            errors.append("setup_role must be ENTRY_AND_MANAGEMENT, ENTRY_ONLY or MANAGEMENT_ONLY")
        entry = self.config.get("entry", {})
        if not isinstance(entry, dict):
            errors.append("entry section must be a mapping")
            entry = {}
        entry_enabled = bool(entry.get("enabled", True))
        errors.extend(entry_policy_errors(self.setup_role, entry_enabled))
        risk = self.config.get("risk")
        if not isinstance(risk, dict):
            errors.append("risk section is required")
        else:
            if self.allows_entry:
                if float(risk.get("max_position_amount_usd", 0) or 0) <= 0:
                    errors.append("risk.max_position_amount_usd must be positive")
                if float(risk.get("max_risk_usd", 0) or 0) <= 0:
                    errors.append("risk.max_risk_usd must be positive")
            trailing = self.config.get("trailing_stop_loss", {})
            trailing_enabled = (
                bool(trailing.get("enabled")) if isinstance(trailing, dict) else False
            )
            if not trailing_enabled:
                errors.append("TRAILING_STOP_LOSS_REQUIRED")
            if self.stop_loss is None or self.stop_loss <= 0:
                errors.append("trailing_stop_loss.initial_stop must be positive")
        if self.allows_entry:
            entry_price = self.worst_case_entry_price()
            if entry_price is None or entry_price <= 0:
                errors.append("estimated entry price is required")
            if entry_price and self.stop_loss and self.stop_loss >= entry_price:
                errors.append("stop loss must be below estimated entry price for long setup")
        return ValidationResult(valid=not errors, errors=errors)

    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        explicit = entry.get("entry_price") or entry.get("trigger_price")
        if explicit is not None:
            return float(explicit)
        zones = self.config.get("zones", {})
        if "breakout_max" in zones:
            return float(zones["breakout_max"])
        return None

    def maximum_limit_price(self) -> float | None:
        entry_price = self.estimated_entry_price()
        if entry_price is None:
            return None
        entry = self.config.get("entry", {})
        if str(entry.get("order_type", "STP_LMT")) != "STP_LMT":
            return None
        if entry.get("maximum_limit_price") is not None:
            return float(entry["maximum_limit_price"])
        if entry.get("limit_price") is not None:
            return float(entry["limit_price"])
        return entry_price + float(entry.get("limit_offset", 0.0) or 0.0)

    def worst_case_entry_price(self) -> float | None:
        return self.maximum_limit_price() or self.estimated_entry_price()

    def entry_zone_label(self) -> str:
        zones = self.config.get("zones", {})
        retest_min = zones.get("retest_min")
        retest_max = zones.get("retest_max")
        if retest_min is not None and retest_max is not None:
            return f"{float(retest_min):.2f}-{float(retest_max):.2f}"
        entry_price = self.estimated_entry_price()
        return f"{entry_price:.2f}" if entry_price else ""

    def to_record(self, status: SetupStatus | None = None) -> SetupRecord:
        return SetupRecord(
            setup_id=self.setup_id,
            symbol=self.symbol,
            setup_type=self.setup_type,
            enabled=self.enabled,
            mode=self.mode,
            status=(status or self.initial_status()).value,
            entry_zone=self.entry_zone_label(),
            stop_loss=self.stop_loss,
            risk_amount=float(self.config.get("risk", {}).get("max_risk_usd", 0) or 0),
            order_status="",
            position_status="",
            last_event="Setup loaded",
            config=self.config,
        )

    @abstractmethod
    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        raise NotImplementedError


def bullish_confirmation(snapshot: MarketSnapshot) -> bool:
    if snapshot.bullish_candle:
        return True
    if snapshot.close is not None and snapshot.open is not None:
        return snapshot.close > snapshot.open
    return False


def _with_legacy_trailing_stop(config: dict[str, Any]) -> dict[str, Any]:
    trailing = config.get("trailing_stop_loss")
    if isinstance(trailing, dict) and trailing.get("enabled") is True:
        return config
    if isinstance(trailing, dict):
        return config
    risk = config.get("risk", {})
    if not isinstance(risk, dict):
        return config
    legacy_stop = risk.get("initial_stop_loss", risk.get("protective_stop"))
    if legacy_stop is None:
        return config
    migrated = deepcopy(config)
    migrated_risk = migrated.get("risk", {})
    if not isinstance(migrated_risk, dict):
        migrated_risk = {}
    migrated_risk.pop("initial_stop_loss", None)
    migrated_risk.pop("protective_stop", None)
    migrated_risk.pop("never_lower_stop", None)
    migrated_risk.pop("trailing_stop_loss", None)
    migrated["risk"] = migrated_risk
    migrated["trailing_stop_loss"] = {
        "enabled": True,
        "mode": "AUTO_INTELLIGENT",
        "initial_stop": legacy_stop,
        "current_stop": legacy_stop,
        "never_lower_stop": True,
        "stop_source": "MIGRATED_FROM_LEGACY_STOP",
        "applies_to": "ENTRY_AND_POSITION_MANAGEMENT",
        "migration_status": "MIGRATED_TO_TRAILING_STOP",
        "activation": {
            "mode": "ON_ENTRY_FILL",
            "activate_before_entry_transmission": True,
            "entry_order_requires_attached_trailing_stop": True,
        },
        "calculation": {
            "method": "HYBRID_ATR_STRUCTURE",
            "allowed_methods": [
                "ATR_BASED",
                "STRUCTURE_BASED",
                "HYBRID_ATR_STRUCTURE",
                "PERCENT_BASED_FALLBACK",
            ],
            "default_method": "HYBRID_ATR_STRUCTURE",
            "atr": {
                "timeframe": "1h",
                "period": 14,
                "multiplier_initial": "AUTO",
                "multiplier_trailing": "AUTO",
                "min_multiplier": 1.0,
                "max_multiplier": 3.5,
            },
            "structure": {
                "reference": "HIGHER_LOW_OR_SUPPORT",
                "allowed_references": [
                    "HIGHER_LOW",
                    "INTRADAY_SUPPORT",
                    "RANGE_LOW",
                    "BROKEN_RESISTANCE_AS_SUPPORT",
                    "VWAP_PULLBACK",
                    "PREVIOUS_DAY_LOW",
                    "AUTO_SELECT",
                ],
                "buffer_policy": "MAX_OF_TICK_SPREAD_ATR_FRACTION",
                "min_tick_buffer": 2,
                "spread_buffer_multiplier": 2,
                "atr_fraction_buffer": 0.1,
            },
            "atr_timeframe": "1h",
            "atr_period": 14,
            "atr_multiplier_initial": "AUTO",
            "atr_multiplier_trailing": "AUTO",
            "structure_reference": "higher_low_or_support",
            "buffer_policy": "MAX_OF_TICK_SPREAD_ATR_FRACTION",
            "min_tick_buffer": 2,
            "spread_buffer_multiplier": 2,
        },
        "ratchet_rules": {
            "enabled": True,
            "move_only_up_for_long": True,
            "move_only_down_for_short": True,
            "update_on_closed_bar_only": True,
            "timeframe": "15m",
            "min_improvement_required": "AUTO",
            "min_improvement_atr_fraction": 0.15,
            "do_not_lower_stop": True,
            "do_not_update_outside_rth": True,
            "do_not_update_if_spread_wide": True,
            "do_not_update_on_unconfirmed_intrabar_move": True,
            "allow_break_even_move": True,
            "break_even_policy": {
                "enabled": True,
                "trigger_after_profit_r_multiple": 1.0,
                "new_stop": "ENTRY_PRICE_PLUS_FEES_BUFFER",
            },
        },
        "broker_order": {
            "order_type": "TRAIL_OR_MANAGED_STOP",
            "attach_to_entry_order": True,
            "required_before_entry_transmission": True,
            "use_native_ibkr_trailing_order_if_available": True,
            "fallback_to_managed_stop_updates": True,
            "parent_child_bracket_required": True,
            "entry_parent_transmit": False,
            "trailing_stop_child_transmit": True,
            "block_if_broker_stop_not_confirmed": True,
        },
    }
    return migrated


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
