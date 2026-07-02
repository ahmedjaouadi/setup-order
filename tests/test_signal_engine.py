from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.engine.signal_engine import SignalEngine
from app.models import MarketSnapshot, OrderRecord, PositionRecord, SetupStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.breakout_retest import BreakoutRetestSetup
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config, valid_momentum_config


class SignalEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"] = {
            "database_file": str(root / "state.sqlite"),
            "setups_folder": str(root / "setups"),
            "logs_folder": str(root / "logs"),
        }
        settings = Settings.from_dict(config)
        self.database = Database(settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.engine = SignalEngine(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_evaluate_snapshot_keeps_auto_off_matching_setups_monitored(self) -> None:
        active_config = valid_momentum_config()
        auto_off_config = {**valid_momentum_config(), "setup_id": "NOK_AUTO_OFF"}
        other_symbol_config = {
            **valid_momentum_config(),
            "setup_id": "UEC_OTHER",
            "symbol": "UEC",
        }
        terminal_config = {**valid_momentum_config(), "setup_id": "NOK_TERMINAL"}

        for config in (
            active_config,
            auto_off_config,
            other_symbol_config,
            terminal_config,
        ):
            self.repository.upsert_setup(MomentumBreakoutSetup(config).to_record())

        self.repository.set_setup_enabled("NOK_AUTO_OFF", False)
        self.repository.update_setup_status(
            "NOK_TERMINAL",
            SetupStatus.CLOSED.value,
            "Closed in test",
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(symbol="NOK", price=15.85, close=15.85),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        self.assertEqual(len(evaluations), 2)
        self.assertEqual(
            {evaluation.setup["setup_id"] for evaluation in evaluations},
            {active_config["setup_id"], auto_off_config["setup_id"]},
        )
        for evaluation in evaluations:
            self.assertEqual(evaluation.processed["status"], "WAITING_ACTIVATION")
            self.assertEqual(evaluation.processed["trace"], {"phase": "test"})
            self.assertIn("entry_decision", evaluation.processed["metadata"])

    def test_processed_signal_exposes_final_entry_decision(self) -> None:
        config = valid_momentum_config()
        self.repository.upsert_setup(MomentumBreakoutSetup(config).to_record())

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="NOK",
                price=16.20,
                bid=16.18,
                ask=16.20,
                open=16.10,
                high=16.22,
                low=16.05,
                close=16.20,
                volume_ratio_closed_bar=1.60,
                average_volume_ratio_last_2_bars=1.20,
                bars_above_resistance=2,
                minimum_tick=0.01,
                atr_15m=0.40,
                atr_1h=0.50,
                support_level=15.05,
                last_confirmed_higher_low=15.05,
                session="RTH",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        decision = evaluations[0].processed["metadata"]["entry_decision"]
        self.assertEqual(decision["status"], "MISSED_BREAKOUT")
        self.assertEqual(decision["decision"], "NO_ENTRY")
        self.assertFalse(decision["can_send_order"])
        self.assertIn("PRICE_TOO_FAR_ABOVE_ENTRY", decision["blocking_reasons"])

    def test_blocks_entry_in_premarket_for_all_setup_types(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
                session="PRE_MARKET",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        processed = evaluations[0].processed
        decision = processed["metadata"]["entry_decision"]
        self.assertEqual(processed["action"], "HOLD")
        self.assertEqual(decision["status"], "PREMARKET_TRIGGER_DETECTED")
        self.assertEqual(decision["next_action"], "PENDING_RTH_CONFIRMATION")
        self.assertFalse(decision["can_send_order"])
        self.assertIn(
            "BLOCKED_OUTSIDE_REGULAR_MARKET_HOURS",
            decision["blocking_reasons"],
        )

    def test_blocks_entry_when_session_is_missing_by_deriving_clock_context(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
                timestamp="2026-06-29T11:46:22+00:00",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        processed = evaluations[0].processed
        decision = processed["metadata"]["entry_decision"]
        self.assertEqual(processed["action"], "HOLD")
        self.assertEqual(decision["status"], "PREMARKET_TRIGGER_DETECTED")
        self.assertFalse(decision["can_send_order"])

    def test_waits_after_rth_open_before_allowing_entry(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
                session="RTH",
                market_open_time="2026-06-13T09:30:00-04:00",
                current_time="2026-06-13T09:35:00-04:00",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        processed = evaluations[0].processed
        decision = processed["metadata"]["entry_decision"]
        self.assertEqual(processed["action"], "HOLD")
        self.assertEqual(decision["status"], "WAITING_AFTER_OPEN_BARS")
        self.assertEqual(decision["next_action"], "WAITING_RTH_CONFIRMATION")
        self.assertFalse(decision["can_send_order"])

    def test_allows_entry_after_rth_wait_window(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
                session="RTH",
                market_open_time="2026-06-13T09:30:00-04:00",
                current_time="2026-06-13T10:15:00-04:00",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        processed = evaluations[0].processed
        decision = processed["metadata"]["entry_decision"]
        self.assertEqual(processed["action"], "ENTRY_READY")
        self.assertEqual(decision["status"], "ENTRY_READY")
        self.assertTrue(decision["can_send_order"])

    def test_blocks_can_send_order_when_trailing_stop_broker_order_not_ready(self) -> None:
        config = valid_breakout_config()
        config["trailing_stop_loss"]["broker_order"]["trailing_stop_order_ready"] = False
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
                session="RTH",
                market_open_time="2026-06-13T09:30:00-04:00",
                current_time="2026-06-13T10:15:00-04:00",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        processed = evaluations[0].processed
        decision = processed["metadata"]["entry_decision"]
        self.assertEqual(processed["action"], "ENTRY_READY")
        self.assertEqual(decision["status"], "BLOCKED_MISSING_PROTECTIVE_STOP_ORDER")
        self.assertFalse(decision["can_send_order"])
        self.assertFalse(decision["protective_stop_order_ready"])
        self.assertIn("PROTECTIVE_STOP_ORDER_NOT_READY", decision["blocking_reasons"])

    def test_blocks_duplicate_active_entry_without_stop(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )
        self.repository.upsert_order(
            OrderRecord(
                id="ord-existing",
                setup_id=config["setup_id"],
                symbol=config["symbol"],
                side="BUY",
                order_type="STP_LMT",
                quantity=6,
                status="SUBMITTED",
                trigger_price=14.44,
                limit_price=14.49,
            )
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
                session="RTH",
                market_open_time="2026-06-13T09:30:00-04:00",
                current_time="2026-06-13T10:15:00-04:00",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        decision = evaluations[0].processed["metadata"]["entry_decision"]
        self.assertEqual(decision["status"], "ACTIVE_ENTRY_ORDER_UNPROTECTED")
        self.assertFalse(decision["can_send_order"])
        self.assertIn("ACTIVE_ORDER_WITHOUT_PROTECTIVE_STOP", decision["blocking_reasons"])

    def test_blocks_open_position_without_stop(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )
        self.repository.upsert_position(
            PositionRecord(
                symbol=config["symbol"],
                setup_id=config["setup_id"],
                quantity=6,
                average_price=14.44,
                current_price=14.60,
                unrealized_pnl=0.96,
                current_stop=13.85,
                risk_remaining=4.5,
                status="OPEN",
            )
        )

        evaluations = self.engine.evaluate_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.35,
                open=14.20,
                high=14.42,
                close=14.38,
                daily_close=14.70,
                bullish_candle=True,
                session="RTH",
                market_open_time="2026-06-13T09:30:00-04:00",
                current_time="2026-06-13T10:15:00-04:00",
            ),
            trace_builder=lambda *_args: {"phase": "test"},
        )

        decision = evaluations[0].processed["metadata"]["entry_decision"]
        self.assertEqual(decision["status"], "POSITION_OPEN_STOP_MISSING_CRITICAL")
        self.assertFalse(decision["can_send_order"])
        self.assertIn("POSITION_OPEN_WITHOUT_PROTECTIVE_STOP", decision["blocking_reasons"])


if __name__ == "__main__":
    unittest.main()
