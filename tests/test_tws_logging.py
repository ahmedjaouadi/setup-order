from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.broker.tws_connector import IbAsyncTwsConnector, _historical_quote_from_bars
from app.engine.trading_engine import TradingEngine
from app.models import ConnectionStatus, MarketSnapshot
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.breakout_retest import BreakoutRetestSetup
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config, valid_momentum_config


def tws_started(request: str, detail: str = "") -> dict:
    return {
        "started": time.perf_counter(),
        "sequence": 1,
        "request": request,
        "detail": detail,
        "sent_at": "2026-06-01T12:00:00+00:00",
    }


class TwsLoggingTests(unittest.TestCase):
    def test_routine_success_logs_are_debug_only(self) -> None:
        connector = IbAsyncTwsConnector("paper", {})

        with self.assertLogs("app.broker.tws_connector", level="DEBUG") as logs:
            connector._record_tws_request_result(
                tws_started("reqCurrentTime", "127.0.0.1:7497 clientId=1001"),
                "OK",
            )

        self.assertTrue(any("DEBUG" in line for line in logs.output))

    def test_stock_market_data_log_includes_quote_fields(self) -> None:
        connector = IbAsyncTwsConnector("paper", {})

        with self.assertLogs("app.broker.tws_connector", level="INFO") as logs:
            connector._record_tws_request_result(
                tws_started("reqTickersAsync", "symbol=UEC snapshot=true"),
                "OK",
                extra={
                    "symbol": "UEC",
                    "price": 14.5,
                    "bid": 14.49,
                    "ask": 14.51,
                    "volume": 120000,
                },
            )

        line = "\n".join(logs.output)
        self.assertIn("symbol=UEC", line)
        self.assertIn("price=14.5", line)
        self.assertIn("bid=14.49", line)
        self.assertIn("ask=14.51", line)
        self.assertIn("volume=120000", line)

    def test_routine_audit_events_are_skipped_from_event_log(self) -> None:
        self.assertTrue(
            TradingEngine._should_skip_tws_audit_event(
                {"request": "accountValues", "status": "OK"}
            )
        )
        self.assertFalse(
            TradingEngine._should_skip_tws_audit_event(
                {"request": "reqTickersAsync", "status": "OK"}
            )
        )
        self.assertFalse(
            TradingEngine._should_skip_tws_audit_event(
                {"request": "reqHistoricalDataAsync", "status": "OK"}
            )
        )
        self.assertFalse(
            TradingEngine._should_skip_tws_audit_event(
                {"request": "reqCurrentTime", "status": "ERROR"}
            )
        )

    def test_stock_quote_message_lists_tws_fields(self) -> None:
        message = TradingEngine._stock_quote_message(
            "UEC",
            {
                "price": 14.5,
                "bid": 14.49,
                "ask": 14.51,
                "last": 14.5,
                "close": 14.42,
                "volume": 120000,
            },
        )

        self.assertEqual(
            message,
            "TWS stock quote UEC: price=14.5 bid=14.49 ask=14.51 "
            "last=14.5 close=14.42 volume=120000",
        )

    def test_historical_ohlcv_bars_become_market_snapshot(self) -> None:
        bars = [
            SimpleNamespace(
                date="20260601",
                open=10.0,
                high=11.0,
                low=9.5,
                close=10.5,
                volume=1000,
            ),
            SimpleNamespace(
                date="20260602",
                open=10.6,
                high=12.0,
                low=10.4,
                close=11.8,
                volume=2000,
            ),
        ]

        snapshot = _historical_quote_from_bars("UEC", "paper", bars)

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["market_data_source"], "historical")
        self.assertEqual(snapshot["price"], 11.8)
        self.assertEqual(snapshot["open"], 10.6)
        self.assertEqual(snapshot["high"], 12.0)
        self.assertEqual(snapshot["low"], 10.4)
        self.assertEqual(snapshot["close"], 11.8)
        self.assertEqual(snapshot["volume"], 2000)
        self.assertEqual(snapshot["previous_high"], 11.0)
        self.assertEqual(snapshot["volume_ratio"], 2.0)
        self.assertEqual(snapshot["bar_date"], "20260602")

    def test_historical_stock_quote_message_lists_ohlcv_fields(self) -> None:
        message = TradingEngine._stock_quote_message(
            "UEC",
            {
                "market_data_source": "historical",
                "price": 11.8,
                "open": 10.6,
                "high": 12.0,
                "low": 10.4,
                "close": 11.8,
                "volume": 2000,
                "previous_high": 11.0,
                "volume_ratio": 2.0,
                "bar_date": "20260602",
                "bar_count": 2,
            },
        )

        self.assertEqual(
            message,
            "TWS stock quote UEC: market_data_source=historical price=11.8 "
            "open=10.6 high=12.0 low=10.4 close=11.8 volume=2000 "
            "previous_high=11.0 volume_ratio=2.0 bar_date=20260602 bar_count=2",
        )


class StockProcessLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"] = {
            "database_file": str(root / "state.sqlite"),
            "setups_folder": str(root / "setups"),
            "logs_folder": str(root / "logs"),
        }
        self.settings = Settings.from_dict(config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.engine = TradingEngine(self.settings, self.repository)

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_force_sync_runs_heartbeat_before_broadcast(self) -> None:
        self.engine.reconciliation = SimpleNamespace(
            run=AsyncMock(return_value={"positions": 0, "orders": 0})
        )
        self.engine._heartbeat = AsyncMock()
        self.engine._broadcast_snapshot = AsyncMock()

        result = await self.engine.force_sync()

        self.assertEqual(result, {"positions": 0, "orders": 0})
        self.engine._heartbeat.assert_awaited_once()
        self.engine._broadcast_snapshot.assert_awaited_once()

    async def test_heartbeat_timestamp_is_recorded_after_stock_poll(self) -> None:
        class SlowQuoteBroker:
            connector_name = "paper"
            account_mode = "paper"
            display_name = "Test broker"
            host = "127.0.0.1"
            port = 7497
            client_id = 1001
            last_error = ""

            async def health_check(self) -> ConnectionStatus:
                return ConnectionStatus.CONNECTED

            async def market_snapshot(self, symbol: str, timeout: float = 4) -> dict:
                return {
                    "available": True,
                    "symbol": symbol,
                    "price": 16.0,
                    "open": 15.9,
                    "high": 16.1,
                    "low": 15.8,
                    "close": 16.0,
                }

            def diagnostics(self) -> dict:
                return {}

            def drain_audit_entries(self) -> list:
                return []

        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record())
        self.engine.broker = SlowQuoteBroker()
        self.engine.order_manager.broker = self.engine.broker
        self.engine.reconciliation.broker = self.engine.broker

        timestamps = [
            "2026-06-04T10:00:00+00:00",
            "2026-06-04T10:00:01+00:00",
            "2026-06-04T10:00:20+00:00",
            "2026-06-04T10:00:30+00:00",
        ]

        def fake_utc_now_iso() -> str:
            if len(timestamps) > 1:
                return timestamps.pop(0)
            return timestamps[0]

        self.engine._utc_now_iso = fake_utc_now_iso

        await self.engine._heartbeat()

        self.assertEqual(
            self.engine._health["last_heartbeat_at"],
            "2026-06-04T10:00:30+00:00",
        )
        self.assertEqual(
            self.engine._health["last_broker_check_at"],
            "2026-06-04T10:00:00+00:00",
        )

    async def test_market_history_uses_selected_chart_timeframe(self) -> None:
        class HistoryBroker:
            def __init__(self) -> None:
                self.calls = []

            async def historical_bars(
                self,
                symbol: str,
                duration: str,
                bar_size: str,
                timeout: float = 4,
            ) -> dict:
                self.calls.append(
                    {
                        "symbol": symbol,
                        "duration": duration,
                        "bar_size": bar_size,
                        "timeout": timeout,
                    }
                )
                return {
                    "available": True,
                    "symbol": symbol,
                    "historical_bars": [],
                }

            def drain_audit_entries(self) -> list:
                return []

        broker = HistoryBroker()
        self.engine.broker = broker

        result = await self.engine.market_history("nok", "10mn")

        self.assertEqual(result["timeframe"], "10m")
        self.assertEqual(result["timeframe_label"], "10mn")
        self.assertEqual(broker.calls[0]["symbol"], "NOK")
        self.assertEqual(broker.calls[0]["duration"], "5 D")
        self.assertEqual(broker.calls[0]["bar_size"], "10 mins")

    async def test_market_history_rejects_unsupported_timeframe(self) -> None:
        with self.assertRaises(ValueError):
            await self.engine.market_history("NOK", "2m")

    async def test_stock_analysis_event_records_hold_decision_and_snapshot(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        self.repository.upsert_setup(setup.to_record())

        processed = await self.engine._analyze_market_snapshot(
            MarketSnapshot(
                symbol="UEC",
                price=14.30,
                close=14.30,
                daily_close=14.30,
            )
        )

        self.assertEqual(len(processed), 1)
        events = self.repository.list_events(symbol="UEC", limit=5)
        analysis_event = next(
            event for event in events if event["event_type"] == "stock_analysis"
        )
        self.assertIn("Stock analysis UEC: 1 setup(s) evaluated", analysis_event["message"])
        self.assertEqual(analysis_event["data"]["snapshot"]["symbol"], "UEC")
        self.assertEqual(analysis_event["data"]["snapshot"]["price"], 14.30)
        self.assertEqual(
            analysis_event["data"]["processed"][0]["setup_id"],
            "UEC_2026_001",
        )
        self.assertEqual(analysis_event["data"]["processed"][0]["action"], "HOLD")
        self.assertEqual(
            analysis_event["data"]["processed"][0]["reason"],
            "Waiting for daily breakout",
        )
        trace = analysis_event["data"]["processed"][0]["trace"]
        self.assertEqual(trace["phase"], "Surveillance activation")
        self.assertTrue(
            any(check["label"] == "Breakout journalier" for check in trace["checks"])
        )

    async def test_missed_breakout_trace_records_limit_guardrail(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record())

        processed = await self.engine._analyze_market_snapshot(
            MarketSnapshot(
                symbol="NOK",
                price=16.20,
                bid=16.18,
                ask=16.20,
                open=16.10,
                high=16.22,
                low=16.08,
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
            )
        )

        self.assertEqual(processed[0]["target_status"], "MISSED_BREAKOUT")
        events = self.repository.list_events(symbol="NOK", limit=5)
        analysis_event = next(
            event for event in events if event["event_type"] == "stock_analysis"
        )
        trace = analysis_event["data"]["processed"][0]["trace"]
        labels = [check["label"] for check in trace["checks"]]
        self.assertIn("Transmission ask <= limite", labels)
        self.assertIn("Setup depasse", labels)


if __name__ == "__main__":
    unittest.main()
