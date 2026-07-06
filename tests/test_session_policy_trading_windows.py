from __future__ import annotations

import unittest

from app.engine.session_policy import (
    TRADING_WINDOW_REASON,
    apply_entry_session_policy,
    execution_window_block,
)
from app.models import MarketSnapshot, SetupSignal, SignalAction


def window_settings(**lunch_overrides) -> dict:
    lunch = {
        "start": "11:30",
        "end": "14:00",
        "mode": "REINFORCED",
        "min_volume_ratio": 1.5,
    }
    lunch.update(lunch_overrides)
    return {
        "session_policy": {
            "enabled": True,
            "require_regular_trading_hours_for_entry": True,
            "wait_after_open_minutes": 0,
            "wait_closed_bars_after_open": 0,
            "trading_windows": {
                "enabled": True,
                "no_entry_before": "10:00",
                "lunch": lunch,
                "no_new_entry_after": "15:30",
            },
        },
    }


def entry_signal() -> SetupSignal:
    return SetupSignal(
        action=SignalAction.ENTRY_READY,
        reason="Breakout confirmed",
        entry_price=20.55,
        stop_loss=19.90,
    )


def rth_snapshot(clock: str, volume_ratio: float | None = None) -> MarketSnapshot:
    # 2026-06-29 is a Monday; times are New York wall clock.
    return MarketSnapshot(
        symbol="ABCD",
        price=20.60,
        session="RTH",
        current_time=f"2026-06-29T{clock}:00-04:00",
        market_open_time="2026-06-29T09:30:00-04:00",
        volume_ratio=volume_ratio,
    )


def decision_status(signal: SetupSignal) -> str:
    metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
    analysis = metadata.get("analysis", {})
    return str(analysis.get("decision_status") or "")


class TradingWindowSignalTests(unittest.TestCase):
    def test_entry_blocked_before_10am(self) -> None:
        signal = apply_entry_session_policy(
            entry_signal(), rth_snapshot("09:45"), window_settings()
        )
        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(decision_status(signal), "ENTRY_BEFORE_TRADING_WINDOW")

    def test_entry_allowed_mid_morning(self) -> None:
        signal = apply_entry_session_policy(
            entry_signal(), rth_snapshot("10:30"), window_settings()
        )
        self.assertEqual(signal.action, SignalAction.ENTRY_READY)

    def test_lunch_blocked_without_rvol(self) -> None:
        signal = apply_entry_session_policy(
            entry_signal(), rth_snapshot("12:30"), window_settings()
        )
        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(decision_status(signal), "LUNCH_WINDOW_RESTRICTED")

    def test_lunch_blocked_with_weak_rvol(self) -> None:
        signal = apply_entry_session_policy(
            entry_signal(),
            rth_snapshot("12:30", volume_ratio=1.1),
            window_settings(),
        )
        self.assertEqual(signal.action, SignalAction.HOLD)

    def test_lunch_allowed_with_strong_rvol(self) -> None:
        signal = apply_entry_session_policy(
            entry_signal(),
            rth_snapshot("12:30", volume_ratio=2.1),
            window_settings(),
        )
        self.assertEqual(signal.action, SignalAction.ENTRY_READY)

    def test_lunch_block_mode_ignores_rvol(self) -> None:
        signal = apply_entry_session_policy(
            entry_signal(),
            rth_snapshot("12:30", volume_ratio=3.0),
            window_settings(mode="BLOCK"),
        )
        self.assertEqual(signal.action, SignalAction.HOLD)

    def test_entry_blocked_after_1530(self) -> None:
        signal = apply_entry_session_policy(
            entry_signal(), rth_snapshot("15:45"), window_settings()
        )
        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(decision_status(signal), "ENTRY_AFTER_CUTOFF")

    def test_windows_disabled_allow_everything(self) -> None:
        settings = window_settings()
        settings["session_policy"]["trading_windows"]["enabled"] = False
        for clock in ("09:45", "12:30", "15:45"):
            signal = apply_entry_session_policy(entry_signal(), rth_snapshot(clock), settings)
            self.assertEqual(signal.action, SignalAction.ENTRY_READY, clock)


class TradingWindowExecutionTests(unittest.TestCase):
    def test_runtime_block_before_window(self) -> None:
        block = execution_window_block(
            window_settings(),
            current_time="2026-06-29T09:45:00-04:00",
        )
        self.assertIsNotNone(block)
        self.assertEqual(block["decision_status"], "ENTRY_BEFORE_TRADING_WINDOW")
        self.assertIn(TRADING_WINDOW_REASON, block["blocking_conditions"])

    def test_runtime_block_after_cutoff(self) -> None:
        block = execution_window_block(
            window_settings(),
            current_time="2026-06-29T15:45:00-04:00",
        )
        self.assertIsNotNone(block)
        self.assertEqual(block["decision_status"], "ENTRY_AFTER_CUTOFF")

    def test_runtime_reinforced_lunch_defers_to_signal_gate(self) -> None:
        # RVOL is unavailable at execution time; the reinforced lunch rule is
        # enforced at signal level, so the runtime gate lets it through.
        block = execution_window_block(
            window_settings(),
            current_time="2026-06-29T12:30:00-04:00",
        )
        self.assertIsNone(block)

    def test_runtime_lunch_block_mode(self) -> None:
        block = execution_window_block(
            window_settings(mode="BLOCK"),
            current_time="2026-06-29T12:30:00-04:00",
        )
        self.assertIsNotNone(block)
        self.assertEqual(block["decision_status"], "LUNCH_WINDOW_RESTRICTED")

    def test_runtime_ok_mid_morning(self) -> None:
        block = execution_window_block(
            window_settings(),
            current_time="2026-06-29T10:30:00-04:00",
        )
        self.assertIsNone(block)


if __name__ == "__main__":
    unittest.main()
