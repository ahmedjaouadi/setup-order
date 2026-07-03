from __future__ import annotations

from typing import Any

from app.models import utc_now_iso
from app.utils.id_generator import new_id


class OpportunityToScenarioMapper:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    def map(self, opportunity: dict[str, Any]) -> dict[str, Any]:
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        market_snapshot = payload.get("market_snapshot") if isinstance(payload.get("market_snapshot"), dict) else {}
        symbol = str(opportunity.get("symbol") or config.get("symbol") or "").upper()
        setup_type = str(
            config.get("setup_type")
            or opportunity.get("opportunity_type")
            or "momentum_breakout"
        )
        scenario_id = _scenario_id(symbol, setup_type)
        trigger, limit_price, resistance = self._entry_levels(
            setup_type,
            config,
            selection,
            market_snapshot,
        )
        risk = config.get("risk") if isinstance(config.get("risk"), dict) else {}
        trailing = (
            config.get("trailing_stop_loss")
            if isinstance(config.get("trailing_stop_loss"), dict)
            else {}
        )
        stop = _first_number(
            trailing.get("initial_stop"),
        )
        max_position = _first_number(
            risk.get("max_position_amount_usd"),
            self.settings.get("risk", {}).get("max_position_amount_usd"),
        )
        max_risk = _first_number(
            risk.get("max_risk_usd"),
            self.settings.get("risk", {}).get("max_risk_per_trade_usd"),
        )
        ambiguities = []
        if stop is None:
            ambiguities.append(
                {
                    "field": "trailing_stop_loss.initial_stop",
                    "reason": "Stop must be confirmed by user or computed from a confirmed risk profile.",
                }
            )
        if trigger is None:
            ambiguities.append(
                {
                    "field": "entry.trigger_price",
                    "reason": "Entry trigger could not be derived from detected levels.",
                }
            )
        scenario = {
            "source_opportunity_id": opportunity.get("opportunity_id"),
            "scenario_id": scenario_id,
            "symbol": symbol,
            "setup_type": setup_type,
            "setup_role": config.get("setup_role", "ENTRY_AND_MANAGEMENT"),
            "direction": config.get("direction", "long"),
            "status": "DRAFT",
            "review_status": "NEEDS_REVIEW" if ambiguities else "READY_FOR_REVIEW",
            "selection": {
                "selected": False,
                "armed": False,
            },
            "breakout": {
                "resistance": resistance,
                "volume_rule_mode": _nested(
                    config,
                    "breakout",
                    "volume_rule_mode",
                )
                or "FLEXIBLE_CONFIRMATION",
            },
            "entry": {
                "enabled": True,
                "order_type": _nested(config, "entry", "order_type") or "STP_LMT",
                "trigger_price": trigger,
                "limit_price": limit_price,
            },
            "risk": {
                "max_position_amount_usd": max_position,
                "max_risk_usd": max_risk,
            },
            "trailing_stop_loss": {
                "enabled": True,
                "mode": "AUTO_INTELLIGENT",
                "never_lower_stop": True,
                "initial_stop": stop,
                "broker_order": {
                    "order_type": "TRAIL_OR_MANAGED_STOP",
                    "attach_to_entry_order": True,
                    "required_before_entry_transmission": True,
                },
            },
            "ambiguities": ambiguities,
            "provenance": {
                "created_from": "opportunity",
                "source_payload_keys": sorted(payload.keys()),
                "created_at": utc_now_iso(),
            },
            "created_at": utc_now_iso(),
        }
        return scenario

    def _entry_levels(
        self,
        setup_type: str,
        config: dict[str, Any],
        selection: dict[str, Any],
        market_snapshot: dict[str, Any],
    ) -> tuple[float | None, float | None, float | None]:
        setup_defaults = self.settings.get("setup_defaults", {})
        entry_defaults = setup_defaults.get("entry", {}) if isinstance(setup_defaults, dict) else {}
        entry = config.get("entry") if isinstance(config.get("entry"), dict) else {}
        trigger_offset = _first_number(entry.get("trigger_offset"), entry_defaults.get("trigger_offset"), 0.02) or 0
        limit_offset = _first_number(entry.get("limit_offset"), entry_defaults.get("limit_offset"), 0.05) or 0
        resistance = _first_number(
            _nested(config, "breakout", "resistance"),
            _nested(config, "breakout", "daily_close_above"),
            _nested(selection, "inputs", "previous_high"),
            market_snapshot.get("previous_high"),
        )
        trigger = _first_number(
            entry.get("trigger_price"),
            entry.get("entry_price"),
        )
        if trigger is None and resistance is not None:
            trigger = resistance + trigger_offset
        limit_price = _first_number(
            entry.get("maximum_limit_price"),
            entry.get("limit_price"),
        )
        if limit_price is None and trigger is not None:
            limit_price = trigger + limit_offset
        if setup_type != "momentum_breakout" and resistance is None:
            resistance = _first_number(market_snapshot.get("previous_high"))
        return _round(trigger), _round(limit_price), _round(resistance)


def _scenario_id(symbol: str, setup_type: str) -> str:
    return f"{symbol}_{setup_type.upper()}_{new_id('draft')}"


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
