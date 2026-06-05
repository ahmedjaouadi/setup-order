from __future__ import annotations

import math
from typing import Any

from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction
from app.setups.base_setup import BaseSetup


class MomentumBreakoutSetup(BaseSetup):
    setup_type = "momentum_breakout"

    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        if entry.get("trigger_price") is not None:
            return float(entry["trigger_price"])
        breakout = self.config.get("breakout", {})
        if breakout.get("resistance") is not None:
            return float(breakout["resistance"]) + float(entry.get("trigger_offset", 0.02))
        return None

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        direction = str(self.config.get("direction", "long")).lower()
        if direction != "long":
            return self._hold(
                "Short momentum breakout is not implemented yet",
                status="INVALID_CONFIGURATION",
                decision="NO_ENTRY",
                missing=[],
                blocking=["direction != long"],
            )
        return self._analyze_long(snapshot, current_status)

    def _analyze_long(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        breakout = _mapping(self.config.get("breakout"))
        entry_config = _mapping(self.config.get("entry"))
        resistance = _first_number(breakout.get("resistance"), self.estimated_entry_price())
        if resistance is None or resistance <= 0:
            return self._hold(
                "Resistance is required",
                status="INVALID_CONFIGURATION",
                decision="NO_ENTRY",
                missing=["breakout.resistance"],
                blocking=[],
            )

        market, missing = self._market_context(snapshot)
        metadata = self._base_metadata(
            snapshot=snapshot,
            resistance=resistance,
            market=market,
            missing=missing,
            blocking=[],
            status="ANALYZING",
            decision="NO_ENTRY",
            next_action="WAIT",
        )
        if missing:
            return SetupSignal(
                action=SignalAction.HOLD,
                reason=f"PAUSED_MISSING_MARKET_DATA: {', '.join(missing)}",
                metadata=metadata,
            )

        spread_check = self._spread_check(market)
        metadata["analysis"]["spread_check"] = spread_check
        if not spread_check["ok"]:
            metadata["analysis"].update(
                {
                    "decision_status": "PAUSED_VOLATILITY_OR_SPREAD_TOO_HIGH",
                    "decision": "NO_ENTRY",
                    "blocking_conditions": spread_check["blocking"],
                    "next_action": "WAIT_FOR_SPREAD",
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason="SPREAD_TOO_WIDE",
                metadata=metadata,
            )

        offsets = self._dynamic_offsets(market)
        metadata["analysis"]["offsets"] = offsets
        if offsets["blocking"]:
            metadata["analysis"].update(
                {
                    "decision_status": "PAUSED_VOLATILITY_OR_SPREAD_TOO_HIGH",
                    "decision": "NO_ENTRY",
                    "blocking_conditions": offsets["blocking"],
                    "next_action": "WAIT_FOR_VOLATILITY_OR_SPREAD",
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason="PAUSED_VOLATILITY_OR_SPREAD_TOO_HIGH",
                metadata=metadata,
            )

        entry_trigger = _round_up_to_tick(
            resistance + offsets["trigger_offset"],
            market["minimum_tick"],
        )
        maximum_limit_price = _round_up_to_tick(
            entry_trigger + offsets["limit_offset"],
            market["minimum_tick"],
        )
        configured_limit = _first_number(
            entry_config.get("maximum_limit_price"),
            entry_config.get("limit_price"),
        )
        if configured_limit is not None:
            maximum_limit_price = min(maximum_limit_price, configured_limit)
        metadata["analysis"].update(
            {
                "trigger_price": entry_trigger,
                "active_trigger_price": entry_trigger,
                "maximum_limit_price": maximum_limit_price,
                "active_limit_price": maximum_limit_price,
                "worst_case_entry_price": maximum_limit_price,
            }
        )
        if maximum_limit_price < entry_trigger:
            metadata["analysis"].update(
                {
                    "decision_status": "INVALID_CONFIGURATION",
                    "decision": "NO_ENTRY",
                    "blocking_conditions": [
                        "maximum_limit_price below entry_trigger",
                    ],
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason="INVALID_CONFIGURATION: maximum limit below trigger",
                metadata=metadata,
            )

        stale = self._stale_state(market, maximum_limit_price)
        metadata["analysis"]["stale"] = stale
        if market["ask"] > maximum_limit_price + stale["buffer"]:
            metadata["analysis"].update(
                {
                    "decision_status": "MISSED_BREAKOUT",
                    "decision": "NO_ENTRY",
                    "next_action": "WAITING_RETEST",
                    "blocking_conditions": ["ask above maximum_limit_price + stale_buffer"],
                }
            )
            return SetupSignal(
                action=SignalAction.STATUS_CHANGE,
                reason="MISSED_BREAKOUT: ask above maximum limit plus stale buffer",
                target_status=SetupStatus.MISSED_BREAKOUT,
                metadata=metadata,
            )

        retest = self._breakout_retest(snapshot, resistance, market)
        metadata["analysis"]["missed_retest"] = retest
        if current_status == SetupStatus.MISSED_BREAKOUT and retest["touched_zone"]:
            metadata["analysis"].update(
                {
                    "decision_status": "WAITING_RETEST",
                    "decision": "NO_ENTRY",
                    "next_action": "CONFIRM_RETEST",
                }
            )
            return SetupSignal(
                action=SignalAction.STATUS_CHANGE,
                reason="Missed breakout retest zone reached",
                target_status=SetupStatus.WAITING_RETEST,
                metadata=metadata,
            )

        validation = self._entry_validation(snapshot, resistance, market, retest)
        metadata["analysis"]["validation"] = validation
        if not validation["valid"]:
            metadata["analysis"].update(
                {
                    "decision_status": "WAITING_CONFIRMATION",
                    "decision": "NO_ENTRY",
                    "missing_conditions": validation["missing"],
                    "blocking_conditions": validation["blocking"],
                    "next_action": "WAIT_FOR_ENTRY_SIGNAL",
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason="WAITING_CONFIRMATION: entry signal is not complete",
                metadata=metadata,
            )

        if market["ask"] > maximum_limit_price:
            metadata["analysis"].update(
                {
                    "decision_status": "PRICE_ALREADY_ABOVE_MAXIMUM_LIMIT",
                    "decision": "NO_ENTRY",
                    "validation_path": validation["path"],
                    "blocking_conditions": ["ask above maximum_limit_price"],
                    "next_action": "WAIT_OR_RETEST",
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason="PRICE_ALREADY_ABOVE_MAXIMUM_LIMIT",
                metadata=metadata,
            )

        stop = self._initial_stop(snapshot, entry_trigger, market)
        metadata["analysis"]["protective_stop"] = stop
        if stop["missing"]:
            metadata["analysis"].update(
                {
                    "decision_status": "PAUSED_MISSING_MARKET_DATA",
                    "decision": "NO_ENTRY",
                    "missing_conditions": stop["missing"],
                    "next_action": "WAIT_FOR_STRUCTURE",
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason=f"PAUSED_MISSING_MARKET_DATA: {', '.join(stop['missing'])}",
                metadata=metadata,
            )

        risk = self._risk_preview(maximum_limit_price, stop["initial_stop_loss"])
        metadata["analysis"]["risk_preview"] = risk
        if risk["risk_per_share"] <= 0:
            metadata["analysis"].update(
                {
                    "decision_status": "INVALID_CONFIGURATION",
                    "decision": "NO_ENTRY",
                    "blocking_conditions": ["risk_per_share <= 0"],
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason="INVALID_CONFIGURATION: risk per share must be positive",
                metadata=metadata,
            )
        if risk["maximum_quantity"] < 1:
            metadata["analysis"].update(
                {
                    "decision_status": "REJECTED_BY_RISK",
                    "decision": "NO_ENTRY",
                    "blocking_conditions": ["maximum_quantity < 1"],
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason="REJECTED_BY_RISK: maximum quantity is zero",
                metadata=metadata,
            )

        metadata["analysis"].update(
            {
                "decision_status": "ENTRY_READY",
                "decision": "ENTRY_ALLOWED",
                "validation_path": validation["path"],
                "missing_conditions": [],
                "blocking_conditions": [],
                "next_action": "SUBMIT_STOP_LIMIT_ORDER",
            }
        )
        metadata["entry_overrides"] = {
            "trigger_offset": offsets["trigger_offset"],
            "limit_offset": offsets["limit_offset"],
            "maximum_limit_price": maximum_limit_price,
        }
        metadata["risk_overrides"] = {"initial_stop_loss": stop["initial_stop_loss"]}
        return SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason=f"Momentum breakout confirmed via {validation['path']}",
            target_status=SetupStatus.ENTRY_READY,
            entry_price=entry_trigger,
            stop_loss=stop["initial_stop_loss"],
            metadata=metadata,
        )

    def _market_context(self, snapshot: MarketSnapshot) -> tuple[dict[str, Any], list[str]]:
        entry = _mapping(self.config.get("entry"))
        missing: list[str] = []
        minimum_tick = _first_number(snapshot.minimum_tick, entry.get("minimum_tick"), 0.01)
        for label, value in (
            ("bid", snapshot.bid),
            ("ask", snapshot.ask),
            ("atr_15m", snapshot.atr_15m),
            ("atr_1h", snapshot.atr_1h),
            ("minimum_tick", minimum_tick),
        ):
            if value is None or value <= 0:
                missing.append(label)
        bid = float(snapshot.bid or 0)
        ask = float(snapshot.ask or 0)
        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else None
        spread = ask - bid if bid > 0 and ask > 0 and ask >= bid else None
        if spread is None:
            missing.append("spread")
        spread_bps = (
            (spread / mid_price) * 10_000
            if spread is not None and mid_price is not None and mid_price > 0
            else None
        )
        return (
            {
                "symbol": snapshot.symbol.upper(),
                "direction": "long",
                "current_price": snapshot.price,
                "bid": bid if bid > 0 else None,
                "ask": ask if ask > 0 else None,
                "spread": spread,
                "mid_price": mid_price,
                "spread_bps": spread_bps,
                "minimum_tick": minimum_tick,
                "atr_15m": snapshot.atr_15m,
                "atr_1h": snapshot.atr_1h,
                "current_bar_volume": snapshot.current_bar_volume or snapshot.volume,
                "volume_ratio_closed_bar": _first_number(
                    snapshot.volume_ratio_closed_bar,
                    snapshot.volume_ratio,
                ),
                "volume_ratio_live": snapshot.volume_ratio_live,
                "average_volume_ratio_last_2_bars": snapshot.average_volume_ratio_last_2_bars,
                "session": snapshot.session,
                "market_open_time": snapshot.market_open_time,
                "current_time": snapshot.current_time or snapshot.timestamp,
            },
            list(dict.fromkeys(missing)),
        )

    def _spread_check(self, market: dict[str, Any]) -> dict[str, Any]:
        liquidity = _mapping(self.config.get("liquidity"))
        entry = _mapping(self.config.get("entry"))
        tier = str(liquidity.get("cap_tier", "mid_cap")).lower()
        defaults = {
            "large_cap": 15,
            "mid_cap": 30,
            "small_cap": 60,
        }
        max_spread_bps = _first_number(
            liquidity.get("max_spread_bps"),
            entry.get("max_spread_bps"),
            defaults.get(tier, 30),
        )
        atr_fraction = _first_number(liquidity.get("max_spread_atr_fraction"), 0.20)
        spread_bps_ok = market["spread_bps"] <= max_spread_bps
        spread_atr_ok = market["spread"] <= atr_fraction * market["atr_15m"]
        blocking = []
        if not spread_bps_ok:
            blocking.append("spread_bps above max")
        if not spread_atr_ok:
            blocking.append("spread above atr fraction")
        return {
            "ok": spread_bps_ok and spread_atr_ok,
            "spread": market["spread"],
            "spread_bps": round(market["spread_bps"], 4),
            "max_spread_bps": max_spread_bps,
            "max_spread_atr": round(atr_fraction * market["atr_15m"], 4),
            "blocking": blocking,
        }

    def _dynamic_offsets(self, market: dict[str, Any]) -> dict[str, Any]:
        tick = market["minimum_tick"]
        spread = market["spread"]
        atr_15m = market["atr_15m"]
        raw_trigger = max(2 * tick, spread, 0.05 * atr_15m)
        trigger_cap = max(2 * tick, 0.20 * atr_15m)
        raw_limit = max(3 * tick, 2 * spread, 0.10 * atr_15m)
        limit_cap = max(3 * tick, 0.35 * atr_15m)
        blocking = []
        if raw_trigger > trigger_cap:
            blocking.append("raw_trigger_offset above cap")
        if raw_limit > limit_cap:
            blocking.append("raw_limit_offset above cap")
        return {
            "mode": "DYNAMIC_GENERIC",
            "raw_trigger_offset": round(raw_trigger, 4),
            "trigger_offset_cap": round(trigger_cap, 4),
            "trigger_offset": _round_up_to_tick(raw_trigger, tick),
            "raw_limit_offset": round(raw_limit, 4),
            "limit_offset_cap": round(limit_cap, 4),
            "limit_offset": _round_up_to_tick(raw_limit, tick),
            "spread": spread,
            "atr_15m": atr_15m,
            "minimum_tick": tick,
            "blocking": blocking,
        }

    def _stale_state(
        self,
        market: dict[str, Any],
        maximum_limit_price: float,
    ) -> dict[str, Any]:
        stale_buffer_raw = max(
            0.50 * market["atr_15m"],
            3 * market["spread"],
            5 * market["minimum_tick"],
        )
        hard_cap = 0.015 * maximum_limit_price
        buffer = min(stale_buffer_raw, hard_cap)
        ask = market["ask"]
        return {
            "rule_type": "PRICE_TOO_FAR_ABOVE_ENTRY",
            "ask": ask,
            "maximum_limit_price": maximum_limit_price,
            "buffer_raw": round(stale_buffer_raw, 4),
            "hard_cap": round(hard_cap, 4),
            "buffer": round(buffer, 4),
            "is_above_limit": ask > maximum_limit_price,
            "is_missed_breakout": ask > maximum_limit_price + buffer,
            "distance_percent": round(((ask - maximum_limit_price) / maximum_limit_price) * 100, 4),
        }

    def _entry_validation(
        self,
        snapshot: MarketSnapshot,
        resistance: float,
        market: dict[str, Any],
        retest: dict[str, Any],
    ) -> dict[str, Any]:
        breakout = _mapping(self.config.get("breakout"))
        volume_closed = market["volume_ratio_closed_bar"]
        avg_last_2 = market["average_volume_ratio_last_2_bars"]
        close = snapshot.close if snapshot.close is not None else snapshot.price
        fast_min = _first_number(breakout.get("fast_breakout_volume_ratio_min"), 1.50)
        confirmed_min = _first_number(
            breakout.get("confirmed_breakout_volume_ratio_min"),
            1.15,
        )
        retest_min = _first_number(breakout.get("retest_volume_ratio_min"), 1.00)
        bars_above = _bars_above_resistance(snapshot, resistance)
        fast = close > resistance and volume_closed is not None and volume_closed >= fast_min
        confirmed = (
            bars_above is not None
            and bars_above >= 2
            and avg_last_2 is not None
            and avg_last_2 >= confirmed_min
        )
        retest_valid = (
            (snapshot.breakout_already_detected or retest["breakout_already_detected"])
            and retest["touched_zone"]
            and close >= resistance
            and snapshot.new_higher_low_confirmed
            and volume_closed is not None
            and volume_closed >= retest_min
        )
        path = ""
        if fast:
            path = "FAST_BREAKOUT"
        elif confirmed:
            path = "CONFIRMED_BREAKOUT"
        elif retest_valid:
            path = "BREAKOUT_RETEST"
        missing = []
        blocking = []
        if volume_closed is None:
            missing.append("volume_ratio_closed_bar")
        if avg_last_2 is None:
            missing.append("average_volume_ratio_last_2_bars")
        if close <= resistance:
            blocking.append("last_closed_bar.close <= resistance")
        if bars_above is None:
            missing.append("bars_above_resistance")
        if not path and volume_closed is not None and volume_closed < retest_min:
            blocking.append("volume_ratio_closed_bar below retest minimum")
        return {
            "valid": bool(path),
            "path": path,
            "fast_breakout_valid": fast,
            "confirmed_breakout_valid": confirmed,
            "breakout_retest_valid": retest_valid,
            "volume_ratio_closed_bar": volume_closed,
            "average_volume_ratio_last_2_bars": avg_last_2,
            "bars_above_resistance": bars_above,
            "missing": list(dict.fromkeys(missing)),
            "blocking": blocking,
            "remaining_confirmation_bars": (
                max(2 - bars_above, 0) if bars_above is not None else None
            ),
        }

    def _breakout_retest(
        self,
        snapshot: MarketSnapshot,
        resistance: float,
        market: dict[str, Any],
    ) -> dict[str, Any]:
        config = _mapping(self.config.get("missed_breakout"))
        retest = _mapping(self.config.get("retest"))
        zone_max = _first_number(
            config.get("retest_zone_max"),
            retest.get("zone_max"),
            resistance + 0.10 * market["atr_15m"],
        )
        zone_min = _first_number(
            config.get("retest_zone_min"),
            retest.get("zone_min"),
            resistance - 0.35 * market["atr_15m"],
        )
        current_low = snapshot.low if snapshot.low is not None else snapshot.price
        touched = current_low <= zone_max
        return {
            "zone_min": _round_down_to_tick(zone_min, market["minimum_tick"]),
            "zone_max": _round_up_to_tick(zone_max, market["minimum_tick"]),
            "current_low": current_low,
            "touched_zone": touched,
            "breakout_already_detected": snapshot.breakout_already_detected,
            "new_higher_low_confirmed": snapshot.new_higher_low_confirmed,
        }

    def _initial_stop(
        self,
        snapshot: MarketSnapshot,
        entry_trigger: float,
        market: dict[str, Any],
    ) -> dict[str, Any]:
        risk = _mapping(self.config.get("risk"))
        supports = [
            snapshot.last_confirmed_higher_low,
            risk.get("last_confirmed_higher_low"),
            snapshot.support_level,
            risk.get("support_level"),
            snapshot.successful_retest_low,
            snapshot.structural_support,
            risk.get("structural_support"),
        ]
        eligible = [
            float(value)
            for value in supports
            if value not in (None, "") and float(value) < entry_trigger
        ]
        missing = []
        if not eligible:
            missing.append("structural_support_below_entry")
            return {"missing": missing, "eligible_supports": []}
        structural_support = max(eligible)
        stop_buffer = max(
            2 * market["minimum_tick"],
            2 * market["spread"],
            0.20 * market["atr_1h"],
        )
        initial_stop = _round_down_to_tick(
            structural_support - stop_buffer,
            market["minimum_tick"],
        )
        return {
            "missing": [],
            "eligible_supports": eligible,
            "structural_support": structural_support,
            "stop_buffer": round(stop_buffer, 4),
            "initial_stop_loss": initial_stop,
            "hard_protective_stop": initial_stop,
        }

    def _risk_preview(
        self,
        worst_case_entry_price: float,
        initial_stop_loss: float,
    ) -> dict[str, Any]:
        risk = _mapping(self.config.get("risk"))
        max_position = _first_number(risk.get("max_position_amount_usd"), 0) or 0
        max_risk = _first_number(risk.get("max_risk_usd"), 0) or 0
        risk_per_share = worst_case_entry_price - initial_stop_loss
        quantity_by_capital = (
            math.floor(max_position / worst_case_entry_price)
            if worst_case_entry_price > 0
            else 0
        )
        quantity_by_risk = (
            math.floor(max_risk / risk_per_share)
            if risk_per_share > 0
            else 0
        )
        maximum_quantity = min(quantity_by_capital, quantity_by_risk)
        return {
            "worst_case_entry_price": worst_case_entry_price,
            "initial_stop_loss": initial_stop_loss,
            "risk_per_share": round(risk_per_share, 4),
            "quantity_by_capital": quantity_by_capital,
            "quantity_by_risk": quantity_by_risk,
            "maximum_quantity": maximum_quantity,
            "maximum_risk": round(maximum_quantity * risk_per_share, 2),
            "max_position_amount_usd": max_position,
            "max_risk_usd": max_risk,
        }

    def _base_metadata(
        self,
        snapshot: MarketSnapshot,
        resistance: float | None,
        market: dict[str, Any] | None,
        missing: list[str],
        blocking: list[str],
        status: str,
        decision: str,
        next_action: str,
    ) -> dict[str, Any]:
        return {
            "analysis": {
                "resistance": resistance,
                "market": market or {},
                "decision_status": status,
                "decision": decision,
                "missing_conditions": missing,
                "blocking_conditions": blocking,
                "next_action": next_action,
            }
        }

    def _hold(
        self,
        reason: str,
        status: str,
        decision: str,
        missing: list[str],
        blocking: list[str],
    ) -> SetupSignal:
        return SetupSignal(
            action=SignalAction.HOLD,
            reason=reason,
            metadata={
                "analysis": {
                    "decision_status": status,
                    "decision": decision,
                    "missing_conditions": missing,
                    "blocking_conditions": blocking,
                    "next_action": "MANUAL_REVIEW",
                }
            },
        )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _round_up_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return round(value, 4)
    return round(math.ceil((value - 1e-12) / tick) * tick, 4)


def _round_down_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return round(value, 4)
    return round(math.floor((value + 1e-12) / tick) * tick, 4)


def _bars_above_resistance(
    snapshot: MarketSnapshot,
    resistance: float,
) -> int | None:
    if snapshot.bars_above_resistance is not None:
        return snapshot.bars_above_resistance
    if not snapshot.historical_bars:
        return None
    count = 0
    for bar in reversed(snapshot.historical_bars):
        if not isinstance(bar, dict):
            break
        close = _first_number(bar.get("close"))
        if close is None or close <= resistance:
            break
        count += 1
    return count
