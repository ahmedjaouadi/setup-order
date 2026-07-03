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
            readiness_status = str(
                market.get("readiness_status") or "PAUSED_MISSING_MARKET_DATA"
            )
            metadata["analysis"].update(
                {
                    "decision_status": readiness_status,
                    "decision": "NO_ENTRY",
                    "missing_conditions": missing,
                    "next_action": "WAIT_FOR_MARKET_DATA",
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason=f"{readiness_status}: {', '.join(missing)}",
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
                    "blocking_conditions": [
                        "PRICE_TOO_FAR_ABOVE_ENTRY",
                        "ASK_ABOVE_MAXIMUM_LIMIT_PLUS_STALE_BUFFER",
                        "ask above maximum_limit_price + stale_buffer",
                    ],
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
            decision_status = self._volume_decision_status(validation)
            metadata["analysis"].update(
                {
                    "decision_status": decision_status,
                    "decision": "NO_ENTRY",
                    "missing_conditions": validation["missing"],
                    "blocking_conditions": validation["blocking"],
                    "next_action": self._volume_next_action(decision_status),
                }
            )
            return SetupSignal(
                action=SignalAction.HOLD,
                reason=f"{decision_status}: entry signal is not complete",
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
        metadata["analysis"]["trailing_stop_loss"] = stop
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

        risk = self._risk_preview(
            maximum_limit_price,
            stop["initial_stop"],
            current_executable_price=market["ask"],
        )
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
        metadata["trailing_stop_overrides"] = {"initial_stop": stop["initial_stop"]}
        return SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason=f"Momentum breakout confirmed via {validation['path']}",
            target_status=SetupStatus.ENTRY_READY,
            entry_price=entry_trigger,
            stop_loss=stop["initial_stop"],
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
        readiness = (
            snapshot.market_data_readiness
            if isinstance(snapshot.market_data_readiness, dict)
            else {}
        )
        for item in readiness.get("missing", []):
            if item in {"bid", "ask", "spread", "live_market_data", "atr_15m", "atr_1h"}:
                missing.append(item)
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
                "current_bar_volume": _first_number(
                    snapshot.bar_volume_15m,
                    snapshot.current_bar_volume,
                    snapshot.volume,
                ),
                "average_bar_volume": snapshot.avg_volume_15m,
                "volume_ratio_closed_bar": _first_number(
                    snapshot.volume_ratio_15m,
                    snapshot.volume_ratio_closed_bar,
                    snapshot.volume_ratio,
                ),
                "volume_ratio_live": snapshot.volume_ratio_live,
                "average_volume_ratio_last_2_bars": snapshot.average_volume_ratio_last_2_bars,
                "volume_status": snapshot.volume_status,
                "volume_timeframe": snapshot.volume_timeframe,
                "volume_comparison_mode": snapshot.volume_comparison_mode,
                "volume_sample_days": snapshot.volume_sample_days,
                "volume_sample_count": snapshot.volume_sample_count,
                "elapsed_bar_percent": snapshot.elapsed_ratio,
                "projected_bar_volume": snapshot.projected_volume,
                "session": snapshot.session,
                "market_open_time": snapshot.market_open_time,
                "current_time": snapshot.current_time or snapshot.timestamp,
                "readiness_status": readiness.get("status"),
                "readiness_missing": readiness.get("missing", []),
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
        volume = self._volume_confirmation(snapshot, resistance, market)
        volume_closed = volume["ratio"]
        avg_last_2 = market["average_volume_ratio_last_2_bars"]
        close = snapshot.close if snapshot.close is not None else snapshot.price
        volume_config = _mapping(self.config.get("volume_confirmation"))
        fast_min = _first_number(
            volume_config.get("fast_volume_ratio_min"),
            breakout.get("fast_breakout_volume_ratio_min"),
            1.50,
        )
        confirmed_min = _first_number(
            volume_config.get("confirmed_volume_ratio_min"),
            breakout.get("confirmed_breakout_volume_ratio_min"),
            0.80,
        )
        retest_min = _first_number(breakout.get("retest_volume_ratio_min"), 1.00)
        hold_bars = int(_first_number(
            volume_config.get("confirmed_hold_bars"),
            breakout.get("confirmed_breakout_hold_bars"),
            2,
        ) or 2)
        bars_above = _bars_above_resistance(snapshot, resistance)
        fast = volume["status"] == "FAST_VOLUME_CONFIRMED"
        progressive_ratio = _first_number(avg_last_2, volume_closed)
        confirmed = (
            bars_above is not None
            and bars_above >= hold_bars
            and close > resistance
            and progressive_ratio is not None
            and progressive_ratio >= confirmed_min
            and volume["status"] not in {"VOLUME_REJECTED", "VOLUME_DATA_MISSING"}
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
        if volume["status"] == "VOLUME_REJECTED":
            blocking.append("volume rejected by price action")
        if volume["status"] == "WEAK_VOLUME":
            blocking.append("volume_ratio below confirmed minimum")
        if volume["status"] == "VOLUME_PENDING_CONFIRMATION" and not confirmed:
            blocking.append("volume pending hold confirmation")
        if not path and volume_closed is not None and volume_closed < retest_min:
            blocking.append("volume_ratio_closed_bar below retest minimum")
        return {
            "valid": bool(path),
            "path": path,
            "fast_breakout_valid": fast,
            "confirmed_breakout_valid": confirmed,
            "breakout_retest_valid": retest_valid,
            "volume_confirmation": volume,
            "volume_status": volume["status"],
            "volume_ratio_closed_bar": volume_closed,
            "average_volume_ratio_last_2_bars": avg_last_2,
            "bars_above_resistance": bars_above,
            "missing": list(dict.fromkeys(missing)),
            "blocking": blocking,
            "remaining_confirmation_bars": (
                max(hold_bars - bars_above, 0) if bars_above is not None else None
            ),
        }

    @staticmethod
    def _volume_decision_status(validation: dict[str, Any]) -> str:
        volume = validation.get("volume_confirmation")
        volume_status = str(volume.get("status") if isinstance(volume, dict) else "")
        close_above_resistance = (
            bool(volume.get("close_above_resistance"))
            if isinstance(volume, dict)
            else False
        )
        if volume_status == "VOLUME_DATA_MISSING":
            return "VOLUME_DATA_MISSING"
        if volume_status == "VOLUME_REJECTED":
            return "BREAKOUT_REJECTED_ON_VOLUME"
        if volume_status == "WEAK_VOLUME" and close_above_resistance:
            return "PRICE_TRIGGERED_WEAK_VOLUME"
        if volume_status == "VOLUME_PENDING_CONFIRMATION":
            return "WAITING_VOLUME_CONFIRMATION"
        return "WAITING_CONFIRMATION"

    @staticmethod
    def _volume_next_action(decision_status: str) -> str:
        if decision_status == "BREAKOUT_REJECTED_ON_VOLUME":
            return "WAIT_FOR_NEW_SETUP_OR_RETEST"
        if decision_status in {"PRICE_TRIGGERED_WEAK_VOLUME", "WAITING_VOLUME_CONFIRMATION"}:
            return "WAIT_FOR_VOLUME_CONFIRMATION_OR_RETEST"
        if decision_status == "VOLUME_DATA_MISSING":
            return "WAIT_FOR_VOLUME_DATA"
        return "WAIT_FOR_ENTRY_SIGNAL"

    def _volume_confirmation(
        self,
        snapshot: MarketSnapshot,
        resistance: float,
        market: dict[str, Any],
    ) -> dict[str, Any]:
        breakout = _mapping(self.config.get("breakout"))
        config = _mapping(self.config.get("volume_confirmation"))
        fast_min = _first_number(
            config.get("fast_volume_ratio_min"),
            breakout.get("fast_breakout_volume_ratio_min"),
            1.50,
        )
        normal_min = _first_number(config.get("normal_volume_ratio_min"), 1.00)
        confirmed_min = _first_number(
            config.get("confirmed_volume_ratio_min"),
            breakout.get("confirmed_breakout_volume_ratio_min"),
            0.80,
        )
        max_upper_wick_ratio = _first_number(config.get("max_upper_wick_ratio"), 0.50)
        reject_enabled = config.get("reject_detection_enabled", True) is not False
        close = _first_number(snapshot.close, snapshot.price)
        high = _first_number(snapshot.high, snapshot.price)
        open_price = _first_number(snapshot.open, close)
        low = _first_number(snapshot.low, close)
        current_volume = market.get("current_bar_volume")
        average_volume = market.get("average_bar_volume")
        elapsed_bar_percent = _first_number(market.get("elapsed_bar_percent"))
        current_bar_is_closed = not (
            elapsed_bar_percent is not None and 0 < elapsed_bar_percent < 0.999
        )
        closed_ratio = _first_number(market.get("volume_ratio_closed_bar"))
        if closed_ratio is None and current_volume not in (None, "") and average_volume not in (None, "") and current_bar_is_closed:
            average = float(average_volume)
            if average > 0:
                closed_ratio = float(current_volume) / average
        projected_volume = _first_number(market.get("projected_bar_volume"))
        projected_ratio = _first_number(market.get("volume_ratio_live"))
        if (
            projected_volume is None
            and current_volume not in (None, "")
            and elapsed_bar_percent is not None
            and elapsed_bar_percent > 0
        ):
            projected_volume = float(current_volume) / elapsed_bar_percent
        if (
            projected_ratio is None
            and projected_volume is not None
            and average_volume not in (None, "")
            and float(average_volume) > 0
        ):
            projected_ratio = projected_volume / float(average_volume)
        ratio = closed_ratio if current_bar_is_closed else _first_number(projected_ratio, closed_ratio)
        candle_range = (high - low) if high is not None and low is not None else 0
        upper_wick = (
            high - max(open_price, close)
            if high is not None and open_price is not None and close is not None
            else 0
        )
        upper_wick_ratio = upper_wick / candle_range if candle_range > 0 else 0
        close_above_resistance = close is not None and close > resistance
        rejected = (
            reject_enabled
            and ratio is not None
            and ratio >= fast_min
            and high is not None
            and high > resistance
            and close is not None
            and close < resistance
            and upper_wick_ratio > max_upper_wick_ratio
        )
        if ratio is None:
            status = "VOLUME_DATA_MISSING"
            interpretation = "Volume ratio unavailable; automatic entry is blocked when volume confirmation is required."
        elif rejected:
            status = "VOLUME_REJECTED"
            interpretation = "Strong volume was detected, but price was rejected below resistance."
        elif ratio >= fast_min and close_above_resistance:
            status = "FAST_VOLUME_CONFIRMED"
            interpretation = "Volume is well above normal and price closed above resistance."
        elif ratio >= normal_min and close_above_resistance:
            status = "VOLUME_CONFIRMED"
            interpretation = "Volume is above normal and supports the breakout."
        elif ratio >= confirmed_min and close_above_resistance:
            status = "VOLUME_PENDING_CONFIRMATION"
            interpretation = "Price is above resistance, but volume still needs hold confirmation."
        else:
            status = "WEAK_VOLUME"
            interpretation = "Volume does not confirm the breakout yet."
        return {
            "status": status,
            "ratio": round(ratio, 4) if ratio is not None else None,
            "closed_bar_volume_ratio": round(closed_ratio, 4) if closed_ratio is not None else None,
            "live_projected_volume_ratio": (
                round(projected_ratio, 4)
                if projected_ratio is not None
                else None
            ),
            "current_bar_volume": current_volume,
            "average_bar_volume": average_volume,
            "current_bar_is_closed": current_bar_is_closed,
            "elapsed_bar_percent": elapsed_bar_percent,
            "projected_bar_volume": (
                round(projected_volume, 4)
                if projected_volume is not None
                else None
            ),
            "sample_count": market.get("volume_sample_count"),
            "timeframe": str(
                config.get("signal_timeframe")
                or market.get("volume_timeframe")
                or breakout.get("confirmed_breakout_timeframe")
                or snapshot.timeframe
            ),
            "comparison_mode": str(
                config.get("comparison_mode")
                or market.get("volume_comparison_mode")
                or "RECENT_BARS"
            ),
            "sample_days": _first_number(
                config.get("average_sample_days"),
                market.get("volume_sample_days"),
            ),
            "liquidity_status": self._liquidity_status(market, current_volume),
            "liquidity": self._liquidity_details(market, current_volume),
            "fast_volume_ratio_min": fast_min,
            "normal_volume_ratio_min": normal_min,
            "confirmed_volume_ratio_min": confirmed_min,
            "close_above_resistance": close_above_resistance,
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "rejected": rejected,
            "interpretation": interpretation,
        }

    def _liquidity_status(
        self,
        market: dict[str, Any],
        current_volume: Any,
    ) -> str:
        details = self._liquidity_details(market, current_volume)
        if not details["bid_ask_available"]:
            return "EXECUTION_LIQUIDITY_MISSING"
        if details["spread_bps"] is not None and details["spread_bps"] > details["max_spread_bps"]:
            return "EXECUTION_LIQUIDITY_WEAK"
        if (
            details["position_vs_dollar_volume_pct"] is not None
            and details["position_vs_dollar_volume_pct"] > details["max_position_vs_dollar_volume_pct"]
        ):
            return "EXECUTION_LIQUIDITY_WEAK"
        return "EXECUTION_LIQUIDITY_OK"

    def _liquidity_details(
        self,
        market: dict[str, Any],
        current_volume: Any,
    ) -> dict[str, Any]:
        risk = _mapping(self.config.get("risk"))
        liquidity = _mapping(self.config.get("liquidity"))
        planned_position = _first_number(risk.get("max_position_amount_usd"), 0) or 0
        max_spread_bps = _first_number(liquidity.get("max_spread_bps"), 30) or 30
        max_position_vs_dollar_volume_pct = _first_number(
            liquidity.get("max_position_vs_dollar_volume_pct"),
            1.0,
        ) or 1.0
        price = _first_number(market.get("current_price"), market.get("ask"), market.get("bid"))
        volume = _first_number(current_volume)
        dollar_volume = price * volume if price is not None and volume is not None else None
        position_vs_dollar_volume_pct = (
            (planned_position / dollar_volume) * 100
            if dollar_volume and dollar_volume > 0
            else None
        )
        return {
            "bid_ask_available": market.get("bid") not in (None, "") and market.get("ask") not in (None, ""),
            "spread_bps": market.get("spread_bps"),
            "max_spread_bps": max_spread_bps,
            "dollar_volume_today": round(dollar_volume, 2) if dollar_volume is not None else None,
            "planned_position_value": planned_position,
            "position_vs_dollar_volume_pct": (
                round(position_vs_dollar_volume_pct, 4)
                if position_vs_dollar_volume_pct is not None
                else None
            ),
            "max_position_vs_dollar_volume_pct": max_position_vs_dollar_volume_pct,
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
            "initial_stop": initial_stop,
        }

    def _risk_preview(
        self,
        worst_case_entry_price: float,
        initial_stop: float,
        *,
        current_executable_price: float | None = None,
    ) -> dict[str, Any]:
        risk = _mapping(self.config.get("risk"))
        max_position = _first_number(risk.get("max_position_amount_usd"), 0) or 0
        max_risk = _first_number(risk.get("max_risk_usd"), 0) or 0
        risk_per_share = worst_case_entry_price - initial_stop
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
        current_risk_per_share = None
        current_max_quantity_by_risk = None
        current_risk_for_planned_quantity = None
        risk_status = "OK"
        if current_executable_price is not None and current_executable_price > 0:
            current_risk_per_share = current_executable_price - initial_stop
            if current_risk_per_share > 0:
                current_max_quantity_by_risk = math.floor(max_risk / current_risk_per_share)
                current_risk_for_planned_quantity = maximum_quantity * current_risk_per_share
                if current_risk_for_planned_quantity > max_risk:
                    risk_status = "BLOCKED_BY_RISK"
            else:
                current_max_quantity_by_risk = 0
                risk_status = "BLOCKED_BY_RISK"
        return {
            "worst_case_entry_price": worst_case_entry_price,
            "current_executable_price": current_executable_price,
            "initial_stop": initial_stop,
            "risk_per_share": round(risk_per_share, 4),
            "current_risk_per_share": (
                round(current_risk_per_share, 4)
                if current_risk_per_share is not None
                else None
            ),
            "quantity_by_capital": quantity_by_capital,
            "quantity_by_risk": quantity_by_risk,
            "maximum_quantity": maximum_quantity,
            "current_max_quantity_by_risk": current_max_quantity_by_risk,
            "maximum_risk": round(maximum_quantity * risk_per_share, 2),
            "current_risk_for_planned_quantity": (
                round(current_risk_for_planned_quantity, 2)
                if current_risk_for_planned_quantity is not None
                else None
            ),
            "max_position_amount_usd": max_position,
            "max_risk_usd": max_risk,
            "risk_status": risk_status,
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
