from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
from typing import Any
import unittest

from app.engine.setup_diagnostics import build_setup_analysis_trace
from app.engine.opportunity_alert_service import OpportunityAlertService
from app.engine.signal_engine import SignalEngine
from app.engine.stock_market_monitor import StockMarketMonitor
from app.market_data.market_data_service import MarketDataService
from app.models import MarketSnapshot, SetupStatus, SignalAction
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.aggressive_rebound import AggressiveReboundSetup
from app.setups.breakout_retest import BreakoutRetestSetup
from app.setups.pullback_continuation import PullbackContinuationSetup
from app.setups.range_breakout import RangeBreakoutSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


def aggressive_rebound_config() -> dict[str, Any]:
    return {
        "setup_id": "AR_2026_001",
        "symbol": "AR",
        "enabled": True,
        "mode": "paper",
        "setup_type": "aggressive_rebound",
        "setup_role": "ENTRY_AND_MANAGEMENT",
        "direction": "long",
        "support_zone": {"min": 9.80, "max": 10.20},
        "invalidation": {"close_below": 9.60},
        "entry": {
            "enabled": True,
            "order_type": "STP_LMT",
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
        },
        "risk": {
            "max_position_amount_usd": 250,
            "max_risk_usd": 15,
            "initial_stop_loss": 9.50,
        },
    }


def range_breakout_config() -> dict[str, Any]:
    return {
        "setup_id": "RB_2026_001",
        "symbol": "RB",
        "enabled": True,
        "mode": "paper",
        "setup_type": "range_breakout",
        "setup_role": "ENTRY_AND_MANAGEMENT",
        "direction": "long",
        "range": {"low": 18.50, "high": 20.00},
        "entry": {
            "enabled": True,
            "order_type": "STP_LMT",
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
        },
        "risk": {
            "max_position_amount_usd": 250,
            "max_risk_usd": 15,
            "initial_stop_loss": 18.40,
        },
    }


def pullback_continuation_config() -> dict[str, Any]:
    return {
        "setup_id": "PB_2026_001",
        "symbol": "PB",
        "enabled": True,
        "mode": "paper",
        "setup_type": "pullback_continuation",
        "setup_role": "ENTRY_AND_MANAGEMENT",
        "direction": "long",
        "pullback": {"entry_reference": 30.00},
        "entry": {
            "enabled": True,
            "order_type": "STP_LMT",
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
        },
        "risk": {
            "max_position_amount_usd": 250,
            "max_risk_usd": 15,
            "initial_stop_loss": 28.90,
        },
    }


class SetupOpportunityDetectionTests(unittest.TestCase):
    def test_aggressive_rebound_detects_support_touch_then_entry(self) -> None:
        setup = AggressiveReboundSetup(aggressive_rebound_config())
        self.assertTrue(setup.validate().valid)

        activation_signal = setup.evaluate(
            MarketSnapshot(symbol="AR", price=10.00, close=10.00),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(activation_signal.action, SignalAction.STATUS_CHANGE)
        self.assertEqual(
            activation_signal.target_status,
            SetupStatus.WAITING_ENTRY_SIGNAL,
        )

        entry_signal = setup.evaluate(
            MarketSnapshot(
                symbol="AR",
                price=10.35,
                open=10.05,
                high=10.32,
                close=10.35,
                previous_high=10.25,
                bullish_candle=True,
            ),
            SetupStatus.WAITING_ENTRY_SIGNAL,
        )

        self.assertEqual(entry_signal.action, SignalAction.ENTRY_READY)
        self.assertEqual(entry_signal.entry_price, 10.27)
        self.assertEqual(entry_signal.stop_loss, 9.50)

    def test_range_breakout_detects_entry_as_soon_as_price_breaks_high(self) -> None:
        setup = RangeBreakoutSetup(range_breakout_config())
        self.assertTrue(setup.validate().valid)

        signal = setup.evaluate(
            MarketSnapshot(symbol="RB", price=20.10, close=20.10),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(signal.action, SignalAction.ENTRY_READY)
        self.assertEqual(signal.entry_price, 20.02)
        self.assertEqual(signal.stop_loss, 18.40)

    def test_pullback_continuation_detects_trend_then_entry(self) -> None:
        setup = PullbackContinuationSetup(pullback_continuation_config())
        self.assertTrue(setup.validate().valid)

        activation_signal = setup.evaluate(
            MarketSnapshot(
                symbol="PB",
                price=31.00,
                close=31.00,
                ema_20=30.50,
                ema_50=29.50,
            ),
            SetupStatus.WAITING_ACTIVATION,
        )

        self.assertEqual(activation_signal.action, SignalAction.STATUS_CHANGE)
        self.assertEqual(
            activation_signal.target_status,
            SetupStatus.WAITING_ENTRY_SIGNAL,
        )

        entry_signal = setup.evaluate(
            MarketSnapshot(
                symbol="PB",
                price=30.40,
                open=30.10,
                high=30.60,
                close=30.55,
                ema_20=30.50,
                ema_50=29.50,
                bullish_candle=True,
            ),
            SetupStatus.WAITING_ENTRY_SIGNAL,
        )

        self.assertEqual(entry_signal.action, SignalAction.ENTRY_READY)
        self.assertEqual(entry_signal.entry_price, 30.62)
        self.assertEqual(entry_signal.stop_loss, 28.90)


class OpportunityPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"] = {
            "database_file": str(root / "state.sqlite"),
            "setups_folder": str(root / "setups"),
            "logs_folder": str(root / "logs"),
        }
        config["market"]["event_deduplication"]["enabled"] = False
        self.settings = Settings.from_dict(config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.signal_engine = SignalEngine(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_signal_engine_keeps_detailed_trace_for_entry_ready(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )

        evaluations = self.signal_engine.evaluate_snapshot(
            breakout_retest_entry_snapshot(),
            build_setup_analysis_trace,
        )

        self.assertEqual(len(evaluations), 1)
        processed = evaluations[0].processed
        self.assertEqual(processed["action"], SignalAction.ENTRY_READY.value)
        self.assertEqual(processed["entry_price"], 14.44)
        self.assertEqual(processed["trace"]["phase"], "Recherche signal entree")
        self.assertEqual(
            processed["trace"]["next_step"],
            "Verifier le risque, construire le bracket entree + stop, puis envoyer l'ordre protege.",
        )
        trace_checks = {
            check["label"]: check["state"]
            for check in processed["trace"]["checks"]
        }
        self.assertEqual(trace_checks["Prix dans zone retest"], "ok")
        self.assertEqual(trace_checks["Bougie de confirmation"], "ok")
        self.assertEqual(trace_checks["Signal entree"], "ok")


class StockMarketMonitorOpportunityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"] = {
            "database_file": str(root / "state.sqlite"),
            "setups_folder": str(root / "setups"),
            "logs_folder": str(root / "logs"),
        }
        config["market"]["event_deduplication"]["enabled"] = False
        self.settings = Settings.from_dict(config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.market_data = MarketDataService()
        self.signal_engine = SignalEngine(self.repository)
        self.opportunity_alert_service = OpportunityAlertService(
            self.repository,
            self.event_store,
            cooldown_seconds=0,
        )
        self.health: dict[str, Any] = {}
        self.handled_signals: list[tuple[dict[str, Any], SetupStatus, Any]] = []

        async def signal_handler(
            setup: dict[str, Any],
            status: SetupStatus,
            signal: Any,
        ) -> None:
            self.handled_signals.append((setup, status, signal))

        self.monitor = StockMarketMonitor(
            settings=self.settings,
            repository=self.repository,
            event_store=self.event_store,
            market_data=self.market_data,
            signal_engine=self.signal_engine,
            signal_handler=signal_handler,
            broker_provider=lambda: None,
            health=self.health,
            audit_drain=lambda: None,
            now_provider=lambda: "2026-06-13T10:00:00+00:00",
            opportunity_alert_service=self.opportunity_alert_service,
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_market_monitor_records_entry_ready_analysis_event(self) -> None:
        config = valid_breakout_config()
        self.repository.upsert_setup(
            BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
        )

        processed = await self.monitor.analyze_market_snapshot(
            breakout_retest_entry_snapshot()
        )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["action"], SignalAction.ENTRY_READY.value)
        self.assertEqual(processed[0]["opportunity_score"]["percent"], 100.0)
        self.assertEqual(len(self.handled_signals), 1)
        self.assertEqual(self.handled_signals[0][2].action, SignalAction.ENTRY_READY)
        self.assertEqual(self.health["last_processed_setups"], 1)
        self.assertEqual(self.market_data.latest("UEC").price, 14.35)

        events = self.repository.list_events(
            symbol="UEC",
            event_type="stock_analysis",
            limit=5,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["processed"][0]["action"], "ENTRY_READY")
        self.assertEqual(
            events[0]["data"]["processed"][0]["opportunity_score"]["label"],
            "READY",
        )
        self.assertGreaterEqual(
            events[0]["data"]["timing"]["analysis_latency_ms"],
            0,
        )
        self.assertGreaterEqual(
            events[0]["data"]["timing"]["evaluation_latency_ms"],
            0,
        )
        self.assertEqual(
            events[0]["data"]["processed"][0]["trace"]["checks"][-2]["label"],
            "Signal entree",
        )
        self.assertEqual(
            events[0]["data"]["processed"][0]["trace"]["checks"][-2]["state"],
            "ok",
        )


def breakout_retest_entry_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
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
    )


if __name__ == "__main__":
    unittest.main()
