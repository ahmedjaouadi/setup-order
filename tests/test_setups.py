from __future__ import annotations

import unittest

from app.models import MarketSnapshot, SetupStatus, SignalAction
from app.setups.breakout_retest import BreakoutRetestSetup
from app.setups.momentum_breakout import MomentumBreakoutSetup


def valid_breakout_config() -> dict:
    return {
        "setup_id": "UEC_2026_001",
        "symbol": "UEC",
        "enabled": True,
        "mode": "simulation",
        "setup_type": "breakout_retest",
        "setup_role": "ENTRY_AND_MANAGEMENT",
        "direction": "long",
        "breakout": {"daily_close_above": 14.50},
        "retest": {"zone_min": 14.10, "zone_max": 14.50, "no_close_below": 14.10},
        "entry": {
            "enabled": True,
            "order_type": "STP_LMT",
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
            "minimum_tick": 0.01,
        },
        "risk": {
            "max_position_amount_usd": 200,
            "max_risk_usd": 15,
            "initial_stop_loss": 13.85,
        },
    }


def valid_momentum_config() -> dict:
    return {
        "setup_id": "NOK_2026_001",
        "symbol": "NOK",
        "enabled": True,
        "mode": "simulation",
        "setup_type": "momentum_breakout",
        "setup_role": "ENTRY_AND_MANAGEMENT",
        "direction": "long",
        "breakout": {
            "resistance": 15.80,
            "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
            "fast_breakout_volume_ratio_min": 1.50,
            "confirmed_breakout_volume_ratio_min": 1.15,
            "confirmed_breakout_hold_bars": 2,
            "confirmed_breakout_timeframe": "15m",
        },
        "missed_breakout": {
            "retest_zone_min": 15.80,
            "retest_zone_max": 15.90,
        },
        "rearm": {
            "new_local_resistance": 16.75,
            "new_trigger": 16.78,
            "new_limit": 16.93,
        },
        "stale_setup": {
            "rule_type": "PRICE_TOO_FAR_ABOVE_ENTRY",
            "max_distance_percent": 1.50,
        },
        "entry": {
            "enabled": True,
            "order_type": "STP_LMT",
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
        },
        "risk": {
            "max_position_amount_usd": 250,
            "max_risk_usd": 15,
            "initial_stop_loss": 14.90,
        },
    }


class BreakoutRetestTests(unittest.TestCase):
    def test_validates_required_safety_fields(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        result = setup.validate()

        self.assertTrue(result.valid)

    def test_rejects_stop_above_estimated_entry(self) -> None:
        config = valid_breakout_config()
        config["risk"]["initial_stop_loss"] = 15.00

        result = BreakoutRetestSetup(config).validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("stop loss" in error for error in result.errors))

    def test_detects_breakout_then_retest_entry(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        breakout_signal = setup.evaluate(
            MarketSnapshot(symbol="UEC", price=14.70, close=14.70, daily_close=14.70),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(breakout_signal.action, SignalAction.STATUS_CHANGE)
        self.assertEqual(breakout_signal.target_status, SetupStatus.WAITING_ENTRY_SIGNAL)

        entry_signal = setup.evaluate(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
            ),
            SetupStatus.WAITING_ENTRY_SIGNAL,
        )

        self.assertEqual(entry_signal.action, SignalAction.ENTRY_READY)
        self.assertEqual(entry_signal.entry_price, 14.44)
        self.assertEqual(entry_signal.stop_loss, 13.85)

    def test_invalidates_close_below_retest_zone(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        signal = setup.evaluate(
            MarketSnapshot(symbol="UEC", price=14.00, close=14.00),
            SetupStatus.WAITING_ENTRY_SIGNAL,
        )

        self.assertEqual(signal.action, SignalAction.INVALIDATE)


class MomentumBreakoutAdaptiveTests(unittest.TestCase):
    def momentum_snapshot(self, **overrides) -> MarketSnapshot:
        data = {
            "symbol": "NOK",
            "price": 15.85,
            "bid": 15.83,
            "ask": 15.85,
            "open": 15.79,
            "high": 15.86,
            "low": 15.81,
            "close": 15.85,
            "volume_ratio_closed_bar": 1.60,
            "average_volume_ratio_last_2_bars": 1.20,
            "bars_above_resistance": 2,
            "minimum_tick": 0.01,
            "atr_15m": 0.40,
            "atr_1h": 0.50,
            "support_level": 15.05,
            "last_confirmed_higher_low": 15.05,
            "session": "RTH",
        }
        data.update(overrides)
        return MarketSnapshot(**data)

    def test_waits_when_price_is_below_resistance(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())

        signal = setup.evaluate(
            self.momentum_snapshot(
                price=15.70,
                bid=15.68,
                ask=15.70,
                close=15.70,
                volume_ratio_closed_bar=1.80,
                bars_above_resistance=0,
            ),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(
            signal.metadata["analysis"]["decision_status"],
            "WAITING_CONFIRMATION",
        )

    def test_allows_confirmed_moderate_volume_after_hold(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())

        signal = setup.evaluate(
            self.momentum_snapshot(volume_ratio_closed_bar=1.20),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(signal.action, SignalAction.ENTRY_READY)
        self.assertEqual(signal.entry_price, 15.82)
        self.assertEqual(
            signal.metadata["analysis"]["validation"]["path"],
            "CONFIRMED_BREAKOUT",
        )

    def test_allows_fast_breakout_with_strong_volume(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())

        signal = setup.evaluate(
            self.momentum_snapshot(
                close=15.86,
                volume_ratio_closed_bar=1.60,
                average_volume_ratio_last_2_bars=1.00,
                bars_above_resistance=1,
            ),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(signal.action, SignalAction.ENTRY_READY)
        self.assertEqual(
            signal.metadata["analysis"]["validation"]["path"],
            "FAST_BREAKOUT",
        )

    def test_rejects_entry_when_ask_is_above_limit(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())

        signal = setup.evaluate(
            self.momentum_snapshot(price=15.90, bid=15.88, ask=15.90),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(
            signal.metadata["analysis"]["decision_status"],
            "PRICE_ALREADY_ABOVE_MAXIMUM_LIMIT",
        )

    def test_marks_missed_breakout_when_price_is_far_above_limit(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())

        signal = setup.evaluate(
            self.momentum_snapshot(price=16.20, bid=16.18, ask=16.20),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(signal.action, SignalAction.STATUS_CHANGE)
        self.assertEqual(signal.target_status, SetupStatus.MISSED_BREAKOUT)

    def test_allows_clean_breakout_retest_entry(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())

        signal = setup.evaluate(
            self.momentum_snapshot(
                price=15.83,
                bid=15.81,
                ask=15.83,
                low=15.82,
                close=15.81,
                volume_ratio_closed_bar=1.05,
                average_volume_ratio_last_2_bars=1.05,
                bars_above_resistance=1,
                breakout_already_detected=True,
                new_higher_low_confirmed=True,
            ),
            SetupStatus.WAITING_RETEST,
        )
        self.assertEqual(signal.action, SignalAction.ENTRY_READY)
        self.assertEqual(
            signal.metadata["analysis"]["validation"]["path"],
            "BREAKOUT_RETEST",
        )

    def test_pauses_when_spread_or_atr_is_missing(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())

        signal = setup.evaluate(
            MarketSnapshot(symbol="NOK", price=15.85, close=15.85),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIn("PAUSED_MISSING_MARKET_DATA", signal.reason)


if __name__ == "__main__":
    unittest.main()
