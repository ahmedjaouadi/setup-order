from __future__ import annotations

import asyncio
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.engine.signal_engine import SignalEngine
from app.engine.stock_market_monitor import (
    active_market_symbols,
    bounded_concurrency,
    quote_to_market_snapshot,
    stock_analysis_dedupe_key,
    stock_quote_message,
)
from app.market_data.market_data_service import MarketDataService
from app.models import BotStatus, ConnectionStatus, SetupStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class StockMarketMonitorHelpersTests(unittest.TestCase):
    def test_quote_to_market_snapshot_maps_tws_quote_fields(self) -> None:
        snapshot = quote_to_market_snapshot(
            "nok",
            {
                "price": "15.85",
                "open": 15.70,
                "high": 15.90,
                "low": 15.60,
                "close": 15.82,
                "bid": 15.83,
                "ask": 15.85,
                "spread_bps": "12.5",
                "volume": 125000,
                "bars_15m_count": "42",
                "bars_1h_count": 12,
                "market_data_source": "hybrid",
                "live_quote_source": "reqMktData",
                "market_data_readiness": {"ready": True},
                "historical_bars": [{"close": 15.82}],
            },
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.symbol, "nok")
        self.assertEqual(snapshot.price, 15.85)
        self.assertEqual(snapshot.close, 15.82)
        self.assertEqual(snapshot.spread_bps, 12.5)
        self.assertEqual(snapshot.bars_15m_count, 42)
        self.assertEqual(snapshot.bars_1h_count, 12)
        self.assertEqual(snapshot.market_data_source, "hybrid")
        self.assertEqual(snapshot.live_quote_source, "reqMktData")
        self.assertEqual(snapshot.market_data_readiness, {"ready": True})
        self.assertEqual(snapshot.historical_bars, [{"close": 15.82}])

    def test_quote_to_market_snapshot_rejects_missing_price(self) -> None:
        self.assertIsNone(quote_to_market_snapshot("NOK", {"available": True}))

    def test_stock_quote_message_lists_market_fields_in_display_order(self) -> None:
        message = stock_quote_message(
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

    def test_active_market_symbols_keeps_auto_off_and_filters_terminal_setups(self) -> None:
        symbols = active_market_symbols(
            [
                {"symbol": "UEC", "enabled": True, "status": SetupStatus.WAITING_ACTIVATION.value},
                {"symbol": "NOK", "enabled": False, "status": SetupStatus.WAITING_ACTIVATION.value},
                {"symbol": "AMD", "enabled": True, "status": SetupStatus.CLOSED.value},
                {
                    "symbol": "UEC",
                    "enabled": True,
                    "status": SetupStatus.WAITING_ENTRY_SIGNAL.value,
                },
            ]
        )

        self.assertEqual(symbols, ["NOK", "UEC"])

    def test_stock_analysis_dedupe_key_includes_blocking_conditions(self) -> None:
        first = stock_analysis_dedupe_key(
            "NOK",
            [
                {
                    "setup_id": "NOK_1",
                    "status": "WAITING_ACTIVATION",
                    "action": "HOLD",
                    "reason": "Waiting",
                    "metadata": {
                        "analysis": {
                            "blocking_conditions": ["spread too wide"],
                        },
                    },
                }
            ],
        )
        second = stock_analysis_dedupe_key(
            "NOK",
            [
                {
                    "setup_id": "NOK_1",
                    "status": "WAITING_ACTIVATION",
                    "action": "HOLD",
                    "reason": "Waiting",
                    "metadata": {
                        "analysis": {
                            "blocking_conditions": ["missing ATR"],
                        },
                    },
                }
            ],
        )

        self.assertNotEqual(first, second)

    def test_bounded_concurrency_respects_symbol_count_and_minimum(self) -> None:
        self.assertEqual(bounded_concurrency(5, 3), 3)
        self.assertEqual(bounded_concurrency(0, 3), 1)
        self.assertEqual(bounded_concurrency(2, 0), 1)


class StockMarketMonitorPollingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"] = {
            "database_file": str(root / "state.sqlite"),
            "setups_folder": str(root / "setups"),
            "logs_folder": str(root / "logs"),
        }
        config["market"]["event_deduplication"]["enabled"] = False
        config["market"]["tws_stock_poll_max_concurrency"] = 3
        self.settings = Settings.from_dict(config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.market_data = MarketDataService()
        self.signal_engine = SignalEngine(self.repository)
        self.broker = ConcurrentFakeBroker(delay_seconds=0.05)

        async def signal_handler(*_args: object) -> None:
            return None

        from app.engine.stock_market_monitor import StockMarketMonitor

        self.monitor = StockMarketMonitor(
            settings=self.settings,
            repository=self.repository,
            event_store=self.event_store,
            market_data=self.market_data,
            signal_engine=self.signal_engine,
            signal_handler=signal_handler,
            broker_provider=lambda: self.broker,
            health={},
            audit_drain=lambda: None,
            now_provider=lambda: "2026-06-14T10:00:00+00:00",
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_poll_active_stock_quotes_collects_symbols_in_parallel_and_logs_timing(
        self,
    ) -> None:
        for symbol in ("AAA", "BBB", "CCC"):
            self.repository.upsert_setup(
                BreakoutRetestSetup(setup_config(symbol)).to_record(SetupStatus.WAITING_ACTIVATION)
            )

        await self.monitor.poll_active_stock_quotes(
            BotStatus.PAUSED.value,
            ConnectionStatus.CONNECTED,
        )

        self.assertGreater(self.broker.max_active, 1)
        self.assertEqual(self.monitor.health["last_stock_poll_max_concurrency"], 3)
        self.assertEqual(self.monitor.health["last_stock_poll_ok"], 3)

        timing_events = self.repository.list_events(
            event_type="stock_poll_timing",
            limit=1,
        )
        self.assertEqual(len(timing_events), 1)
        timing = timing_events[0]["data"]
        self.assertEqual(timing["max_concurrency"], 3)
        self.assertEqual(len(timing["timings"]), 3)
        self.assertGreaterEqual(timing["quote_latency_ms"]["count"], 3)
        self.assertGreater(timing["cycle_latency_ms"], 0)

        quote_events = self.repository.list_events(event_type="stock_quote", limit=5)
        self.assertEqual(len(quote_events), 3)
        for event in quote_events:
            self.assertIn("quote_latency_ms", event["data"]["timing"])


class ConcurrentFakeBroker:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.active = 0
        self.max_active = 0

    async def market_snapshot(self, symbol: str, timeout: float = 4.0) -> dict[str, object]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay_seconds)
            return {
                "available": True,
                "symbol": symbol.upper(),
                "price": 10.0,
                "close": 10.0,
                "market_data_source": "test",
                "timeout": timeout,
            }
        finally:
            self.active -= 1


def setup_config(symbol: str) -> dict[str, object]:
    config = deepcopy(valid_breakout_config())
    config["setup_id"] = f"{symbol}_2026_001"
    config["symbol"] = symbol
    return config


if __name__ == "__main__":
    unittest.main()
