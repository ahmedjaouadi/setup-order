from __future__ import annotations

import math

from app.models import (
    MarketSnapshot,
    SetupRole,
    SetupSignal,
    SetupStatus,
    SignalAction,
    ValidationResult,
)
from app.setups.base_setup import BaseSetup


class PositionManagementSetup(BaseSetup):
    setup_type = "position_management"

    @property
    def setup_role(self) -> SetupRole:
        return SetupRole.MANAGEMENT_ONLY

    def estimated_entry_price(self) -> float | None:
        return None

    def validate(self) -> ValidationResult:
        result = super().validate()
        errors = list(result.errors)
        if (
            self.config.get("setup_role", SetupRole.MANAGEMENT_ONLY.value)
            != SetupRole.MANAGEMENT_ONLY.value
        ):
            errors.append("position_management setup_role must be MANAGEMENT_ONLY")
        source = self.config.get("position_source", {})
        if source.get("mode") != "adopt_existing_ibkr_position":
            errors.append(
                "position_source.mode must be adopt_existing_ibkr_position"
            )
        if not bool(source.get("require_existing_position", True)):
            errors.append("position_source.require_existing_position must be true")
        return ValidationResult(valid=not errors, errors=errors)

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        if current_status == SetupStatus.RECONCILING_EXISTING_POSITION:
            return SetupSignal.hold("Waiting for reconciliation with IBKR")
        if current_status not in {SetupStatus.IN_POSITION, SetupStatus.MANAGING_POSITION}:
            return SetupSignal.hold("No adopted position to manage")

        rule = self._next_stop_rule(snapshot)
        if rule is None:
            return SetupSignal.hold("No management rule reached")
        metadata = {"rule_id": rule["rule_id"]}
        rule_metadata = rule.get("metadata")
        if isinstance(rule_metadata, dict):
            metadata.update(rule_metadata)
        return SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason=f"Management rule reached: {rule['rule_id']}",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=float(rule["new_stop"]),
            metadata=metadata,
        )

    def _next_stop_rule(self, snapshot: MarketSnapshot) -> dict[str, object] | None:
        stop_management = (
            self.config.get("management", {})
            .get("stop_management", {})
        )
        mode = str(stop_management.get("mode", "")).lower()
        if mode == "structure_based_trailing":
            return self._structure_based_trailing_stop(snapshot, stop_management)

        rules = stop_management.get("rules")
        if not isinstance(rules, list):
            rules = [
                {
                    "rule_id": step.get("step_id") or step.get("name") or "stop_step",
                    "when": {
                        "metric": "last_price",
                        "operator": ">=",
                        "value": step.get("when_price_above"),
                    },
                    "action": {"type": "raise_stop", "value": step.get("new_stop")},
                }
                for step in stop_management.get("steps", [])
            ]
        candidates: list[dict[str, object]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            when = rule.get("when", {})
            action = rule.get("action", {})
            if not isinstance(when, dict) or not isinstance(action, dict):
                continue
            if action.get("type") != "raise_stop":
                continue
            if action.get("value") is None:
                continue
            metric = str(when.get("metric", "last_price"))
            operator = str(when.get("operator", ">="))
            value = when.get("value")
            if value is None:
                continue
            if _metric_value(snapshot, metric) is None:
                continue
            if _compare(float(_metric_value(snapshot, metric)), operator, float(value)):
                candidates.append(
                    {
                        "rule_id": str(rule.get("rule_id", "stop_rule")),
                        "new_stop": float(action["value"]),
                    }
                )
        if not candidates:
            return None
        return max(candidates, key=lambda item: float(item["new_stop"]))

    def _structure_based_trailing_stop(
        self,
        snapshot: MarketSnapshot,
        stop_management: dict[str, object],
    ) -> dict[str, object] | None:
        raise_stop_only_if = stop_management.get("raise_stop_only_if", {})
        if (
            not isinstance(raise_stop_only_if, dict)
            or bool(raise_stop_only_if.get("new_higher_low_confirmed", True))
        ) and not snapshot.new_higher_low_confirmed:
            return None

        support = _first_float(
            snapshot.last_confirmed_higher_low,
            snapshot.structural_support,
            snapshot.support_level,
        )
        bid = _first_float(snapshot.bid)
        ask = _first_float(snapshot.ask)
        tick = _first_float(snapshot.minimum_tick)
        atr_1h = _first_float(snapshot.atr_1h)
        if (
            support is None
            or bid is None
            or ask is None
            or tick is None
            or atr_1h is None
        ):
            return None
        if support <= 0 or bid <= 0 or ask <= 0 or tick <= 0 or atr_1h <= 0:
            return None
        if ask < bid:
            return None
        if support >= snapshot.price:
            return None

        spread = ask - bid
        stop_buffer = max(2 * tick, 2 * spread, 0.20 * atr_1h)
        new_stop = _round_down_to_tick(support - stop_buffer, tick)
        if new_stop <= 0:
            return None
        return {
            "rule_id": "structure_based_trailing",
            "new_stop": new_stop,
            "metadata": {
                "structural_support": support,
                "stop_buffer": round(stop_buffer, 6),
                "spread": round(spread, 6),
                "atr_1h": atr_1h,
                "minimum_tick": tick,
            },
        }


def _metric_value(snapshot: MarketSnapshot, metric: str) -> float | None:
    values = {
        "last_price": snapshot.price,
        "candle_open": snapshot.open,
        "candle_high": snapshot.high,
        "candle_low": snapshot.low,
        "candle_close": snapshot.close,
        "previous_candle_high": snapshot.previous_high,
        "volume_ratio": snapshot.volume_ratio,
    }
    return values.get(metric)


def _first_float(*values: object) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _round_down_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return round(math.floor((value / tick) + 1e-9) * tick, 6)


def _compare(left: float, operator: str, right: float) -> bool:
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return left == right
    return False
