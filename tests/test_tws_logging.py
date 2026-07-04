from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.broker.ib_models import BrokerOrderRequest
from app.broker.tws_connector import (
    IbAsyncTwsConnector,
    _historical_quote_from_bars,
    _merge_hybrid_market_snapshot,
    _trade_status_detail,
    _tws_order_status_to_order_status,
    _utc_now_iso,
    calculate_spread,
)
from app.engine.trading_engine import TradingEngine
from app.models import ConnectionStatus, EventLevel, EventRecord, MarketSnapshot, OrderStatus
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

    def test_repository_can_filter_tws_request_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = deepcopy(DEFAULT_CONFIG)
            config["storage"] = {
                "database_file": str(root / "state.sqlite"),
                "setups_folder": str(root / "setups"),
                "logs_folder": str(root / "logs"),
            }
            settings = Settings.from_dict(config)
            database = Database(settings.database_file)
            database.initialize()
            repository = TradingRepository(database)
            try:
                repository.add_event(
                    EventRecord(
                        timestamp="2026-06-01T12:00:00+00:00",
                        level=EventLevel.INFO.value,
                        event_type="tws_request",
                        message="TWS request OK",
                        data={"request": "reqCurrentTime"},
                    )
                )
                repository.add_event(
                    EventRecord(
                        timestamp="2026-06-01T12:00:01+00:00",
                        level=EventLevel.INFO.value,
                        event_type="stock_analysis",
                        message="Stock analysis NOK: 1 setup(s) evaluated",
                        symbol="NOK",
                    )
                )

                events = repository.list_events(limit=10, event_type="tws_request")
            finally:
                database.close()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "tws_request")
        self.assertEqual(events[0]["data"]["request"], "reqCurrentTime")

    def test_stock_contract_uses_primary_exchange_override(self) -> None:
        class FakeStock:
            def __init__(
                self,
                symbol: str,
                exchange: str,
                currency: str,
                primaryExchange: str = "",
            ) -> None:
                self.symbol = symbol
                self.exchange = exchange
                self.currency = currency
                self.primaryExchange = primaryExchange

        connector = IbAsyncTwsConnector(
            "paper",
            {
                "stock_exchange": "SMART",
                "primary_exchange_by_symbol": {"NOK": "NYSE"},
            },
        )

        with patch.dict("sys.modules", {"ib_async": SimpleNamespace(Stock=FakeStock)}):
            contract = connector._stock_contract("nok")

        self.assertEqual(contract.symbol, "NOK")
        self.assertEqual(contract.exchange, "SMART")
        self.assertEqual(contract.currency, "USD")
        self.assertEqual(contract.primaryExchange, "NYSE")

    def test_build_order_preserves_bracket_parent_and_transmit_flags(self) -> None:
        class FakeBaseOrder:
            def __init__(self, action: str, quantity: int) -> None:
                self.action = action
                self.totalQuantity = quantity
                self.parentId = 0
                self.ocaGroup = ""
                self.transmit = True

        class FakeMarketOrder(FakeBaseOrder):
            pass

        class FakeLimitOrder(FakeBaseOrder):
            def __init__(self, action: str, quantity: int, limit_price: float) -> None:
                super().__init__(action, quantity)
                self.lmtPrice = limit_price

        class FakeStopOrder(FakeBaseOrder):
            def __init__(self, action: str, quantity: int, stop_price: float) -> None:
                super().__init__(action, quantity)
                self.auxPrice = stop_price

        class FakeStopLimitOrder(FakeBaseOrder):
            def __init__(
                self,
                action: str,
                quantity: int,
                limit_price: float,
                stop_price: float,
            ) -> None:
                super().__init__(action, quantity)
                self.lmtPrice = limit_price
                self.auxPrice = stop_price

        connector = IbAsyncTwsConnector("paper", {})

        with patch.dict(
            "sys.modules",
            {
                "ib_async": SimpleNamespace(
                    LimitOrder=FakeLimitOrder,
                    MarketOrder=FakeMarketOrder,
                    StopLimitOrder=FakeStopLimitOrder,
                    StopOrder=FakeStopOrder,
                )
            },
        ):
            parent = connector._build_order(
                BrokerOrderRequest(
                    client_order_id="ord_1",
                    setup_id="SETUP_1",
                    symbol="NOK",
                    side="BUY",
                    order_type="STP_LMT",
                    quantity=2,
                    trigger_price=15.10,
                    limit_price=15.15,
                    oca_group="bracket:SETUP_1",
                    transmit=False,
                )
            )
            stop = connector._build_order(
                BrokerOrderRequest(
                    client_order_id="stp_1",
                    setup_id="SETUP_1",
                    symbol="NOK",
                    side="SELL",
                    order_type="STP",
                    quantity=2,
                    stop_price=14.40,
                    parent_id="123",
                    oca_group="bracket:SETUP_1",
                    transmit=True,
                )
            )

        self.assertEqual(parent.auxPrice, 15.10)
        self.assertEqual(parent.lmtPrice, 15.15)
        self.assertEqual(parent.ocaGroup, "bracket:SETUP_1")
        self.assertFalse(parent.transmit)
        self.assertEqual(stop.auxPrice, 14.40)
        self.assertEqual(stop.parentId, 123)
        self.assertEqual(stop.ocaGroup, "bracket:SETUP_1")
        self.assertTrue(stop.transmit)

    def test_tws_cancelled_status_is_not_stored_as_submitted(self) -> None:
        self.assertEqual(
            _tws_order_status_to_order_status("Cancelled"),
            OrderStatus.CANCELLED.value,
        )
        self.assertEqual(
            _tws_order_status_to_order_status("Inactive"),
            OrderStatus.REJECTED.value,
        )
        self.assertEqual(
            _tws_order_status_to_order_status("PreSubmitted"),
            OrderStatus.SUBMITTED.value,
        )

    def test_trade_status_detail_collects_order_log_messages(self) -> None:
        trade = SimpleNamespace(
            advancedError="",
            log=[
                SimpleNamespace(message=""),
                SimpleNamespace(
                    message="Error 10349, reqId 5618: Order TIF was set to DAY based on order preset"
                ),
                SimpleNamespace(
                    message="Error 10349, reqId 5618: Order TIF was set to DAY based on order preset"
                ),
            ],
        )

        detail = _trade_status_detail(trade)

        self.assertIn("Error 10349", detail)
        self.assertEqual(detail.count("Error 10349"), 1)

    def test_hybrid_signal_cache_uses_dedicated_short_ttl(self) -> None:
        connector = IbAsyncTwsConnector(
            "paper",
            {
                "market_data_ttl": {
                    "hybrid_signal_seconds": 20,
                    "atr_15m_seconds": 1200,
                    "atr_1h_seconds": 5400,
                    "historical_seconds": 300,
                }
            },
        )

        self.assertEqual(
            connector._historical_cache_ttl_seconds(
                "15 mins",
                cache_profile="hybrid_signal",
            ),
            20,
        )
        self.assertEqual(connector._historical_cache_ttl_seconds("15 mins"), 1200)

    def test_live_market_snapshot_uses_async_safe_client_api(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.market_data_type = None
                self.request = None
                self.cancelled = None

            def reqMarketDataType(self, market_data_type: int) -> None:
                self.market_data_type = market_data_type

            def getReqId(self) -> int:
                return 42

            def reqMktData(
                self,
                req_id: int,
                contract: object,
                generic_tick_list: str,
                snapshot: bool,
                regulatory_snapshot: bool,
                market_data_options: list,
            ) -> None:
                self.request = {
                    "req_id": req_id,
                    "contract": contract,
                    "generic_tick_list": generic_tick_list,
                    "snapshot": snapshot,
                    "regulatory_snapshot": regulatory_snapshot,
                    "market_data_options": market_data_options,
                }

            def cancelMktData(self, req_id: int) -> None:
                self.cancelled = req_id

        class FakeWrapper:
            def __init__(self) -> None:
                self.ticker = SimpleNamespace(
                    bid=14.0,
                    ask=14.02,
                    last=14.01,
                    marketDataType=1,
                    open=None,
                    high=None,
                    low=None,
                    close=None,
                    volume=1000,
                )
                self.started = None
                self.ended = None

            def startTicker(self, req_id: int, contract: object, tick_type: str):
                self.started = (req_id, contract, tick_type)
                return self.ticker

            def endTicker(self, ticker: object, tick_type: str) -> int:
                self.ended = (ticker, tick_type)
                return 42

        class FakeIb:
            def __init__(self) -> None:
                self.client = FakeClient()
                self.wrapper = FakeWrapper()

            def reqMktData(self, *args, **kwargs):
                raise AssertionError("sync IB.reqMktData must not be used")

            def reqMarketDataType(self, *args, **kwargs):
                raise AssertionError("sync IB.reqMarketDataType must not be used")

        connector = IbAsyncTwsConnector(
            "paper",
            {"market_data_type": 1, "live_quote_wait_seconds": 0.1},
        )
        connector._ib = FakeIb()

        snapshot = asyncio.run(
            connector._ticker_market_snapshot(
                "NOK",
                SimpleNamespace(symbol="NOK"),
                timeout=1,
            )
        )

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["bid"], 14.0)
        self.assertEqual(snapshot["ask"], 14.02)
        self.assertEqual(snapshot["spread"], 0.02)
        self.assertEqual(connector._ib.client.market_data_type, 1)
        self.assertEqual(connector._ib.client.request["req_id"], 42)
        self.assertFalse(connector._ib.client.request["snapshot"])
        self.assertIsNone(connector._ib.client.cancelled)
        self.assertEqual(connector._ib.wrapper.started[2], "mktData")
        self.assertIsNone(connector._ib.wrapper.ended)

        connector._unsubscribe_live_quotes()
        self.assertEqual(connector._ib.client.cancelled, 42)
        self.assertEqual(connector._ib.wrapper.ended[1], "mktData")

    def test_live_market_snapshot_tries_delayed_fallback_when_live_is_empty(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.market_data_type = 1
                self.next_id = 100
                self.requests = []
                self.cancelled = []

            def reqMarketDataType(self, market_data_type: int) -> None:
                self.market_data_type = market_data_type

            def getReqId(self) -> int:
                self.next_id += 1
                return self.next_id

            def reqMktData(
                self,
                req_id: int,
                contract: object,
                generic_tick_list: str,
                snapshot: bool,
                regulatory_snapshot: bool,
                market_data_options: list,
            ) -> None:
                self.requests.append((req_id, self.market_data_type, snapshot))

            def cancelMktData(self, req_id: int) -> None:
                self.cancelled.append(req_id)

        class FakeWrapper:
            def __init__(self, client: FakeClient) -> None:
                self.client = client

            def startTicker(self, req_id: int, contract: object, tick_type: str):
                if self.client.market_data_type == 3:
                    return SimpleNamespace(
                        bid=14.0,
                        ask=14.02,
                        last=14.01,
                        marketDataType=3,
                        open=None,
                        high=None,
                        low=None,
                        close=None,
                        volume=1000,
                    )
                return SimpleNamespace(
                    bid=None,
                    ask=None,
                    last=None,
                    marketDataType=1,
                    open=None,
                    high=None,
                    low=None,
                    close=None,
                    volume=None,
                )

            def endTicker(self, ticker: object, tick_type: str) -> int:
                return 101 if getattr(ticker, "marketDataType", None) == 1 else 102

        class FakeIb:
            def __init__(self) -> None:
                self.client = FakeClient()
                self.wrapper = FakeWrapper(self.client)

        connector = IbAsyncTwsConnector(
            "paper",
            {
                "market_data_type": 1,
                "market_data_type_fallbacks": [3],
                "live_quote_wait_seconds": 0.1,
            },
        )
        connector._ib = FakeIb()

        snapshot = asyncio.run(
            connector._ticker_market_snapshot(
                "NOK",
                SimpleNamespace(symbol="NOK"),
                timeout=1,
            )
        )

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["market_data_type_requested"], 3)
        self.assertEqual(snapshot["market_data_type_actual"], 3)
        self.assertEqual(snapshot["bid"], 14.0)
        self.assertEqual(snapshot["ask"], 14.02)
        self.assertEqual(snapshot["spread"], 0.02)
        self.assertEqual(connector._ib.client.requests, [(101, 1, False), (102, 3, False)])
        self.assertEqual(connector._ib.client.cancelled, [])
        connector._unsubscribe_live_quotes()
        self.assertEqual(connector._ib.client.cancelled, [101, 102])

    def test_live_market_snapshot_reports_timeout_when_bid_ask_are_missing(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.market_data_type = None

            def reqMarketDataType(self, market_data_type: int) -> None:
                self.market_data_type = market_data_type

            def getReqId(self) -> int:
                return 77

            def reqMktData(
                self,
                req_id: int,
                contract: object,
                generic_tick_list: str,
                snapshot: bool,
                regulatory_snapshot: bool,
                market_data_options: list,
            ) -> None:
                return None

            def cancelMktData(self, req_id: int) -> None:
                return None

        class FakeWrapper:
            def __init__(self) -> None:
                self.ticker = SimpleNamespace(
                    bid=None,
                    ask=None,
                    last=20.45,
                    marketDataType=1,
                    open=None,
                    high=None,
                    low=None,
                    close=None,
                    volume=None,
                )

            def startTicker(self, req_id: int, contract: object, tick_type: str):
                return self.ticker

            def endTicker(self, ticker: object, tick_type: str) -> int:
                return 77

        class FakeIb:
            def __init__(self) -> None:
                self.client = FakeClient()
                self.wrapper = FakeWrapper()

        connector = IbAsyncTwsConnector(
            "paper",
            {"market_data_type": 1, "live_quote_wait_seconds": 0.05},
        )
        connector._ib = FakeIb()

        snapshot = asyncio.run(
            connector._ticker_market_snapshot_for_type(
                "NOK",
                SimpleNamespace(symbol="NOK"),
                timeout=1,
                market_data_type=1,
            )
        )

        self.assertFalse(snapshot["available"])
        self.assertIn("LIVE_QUOTE_TIMEOUT", snapshot["message"])
        self.assertEqual(snapshot["last"], 20.45)
        self.assertIsNone(snapshot["bid"])
        self.assertEqual(connector._diagnostics["last_tws_request_status"], "ERROR")
        diagnostics = connector.market_data_diagnostics("NOK")
        self.assertEqual(diagnostics["subscription"]["active_subscription_count"], 1)

    def test_live_market_snapshot_reports_empty_quote_when_no_fields_arrive(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.market_data_type = None

            def reqMarketDataType(self, market_data_type: int) -> None:
                self.market_data_type = market_data_type

            def getReqId(self) -> int:
                return 88

            def reqMktData(
                self,
                req_id: int,
                contract: object,
                generic_tick_list: str,
                snapshot: bool,
                regulatory_snapshot: bool,
                market_data_options: list,
            ) -> None:
                return None

            def cancelMktData(self, req_id: int) -> None:
                return None

        class FakeWrapper:
            def __init__(self) -> None:
                self.ticker = SimpleNamespace(
                    bid=None,
                    ask=None,
                    last=None,
                    marketDataType=1,
                    open=None,
                    high=None,
                    low=None,
                    close=None,
                    volume=None,
                )

            def startTicker(self, req_id: int, contract: object, tick_type: str):
                return self.ticker

            def endTicker(self, ticker: object, tick_type: str) -> int:
                return 88

        class FakeIb:
            def __init__(self) -> None:
                self.client = FakeClient()
                self.wrapper = FakeWrapper()

        connector = IbAsyncTwsConnector(
            "paper",
            {"market_data_type": 1, "live_quote_wait_seconds": 0.05},
        )
        connector._ib = FakeIb()

        snapshot = asyncio.run(
            connector._ticker_market_snapshot_for_type(
                "NOK",
                SimpleNamespace(symbol="NOK"),
                timeout=1,
                market_data_type=1,
            )
        )

        self.assertFalse(snapshot["available"])
        self.assertIn("EMPTY_QUOTE", snapshot["message"])
        self.assertEqual(snapshot["quote_state"], "EMPTY_QUOTE")
        self.assertIn("price", snapshot["missing_fields"])
        self.assertIn("bid", snapshot["missing_fields"])
        self.assertIn("ask", snapshot["missing_fields"])
        self.assertEqual(
            connector._diagnostics["last_live_quote_extra"]["quote_state"],
            "EMPTY_QUOTE",
        )

    def test_stale_empty_live_subscription_is_reset_before_retry(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.market_data_type = None
                self.next_id = 90
                self.requests = []
                self.cancelled = []

            def reqMarketDataType(self, market_data_type: int) -> None:
                self.market_data_type = market_data_type

            def getReqId(self) -> int:
                self.next_id += 1
                return self.next_id

            def reqMktData(
                self,
                req_id: int,
                contract: object,
                generic_tick_list: str,
                snapshot: bool,
                regulatory_snapshot: bool,
                market_data_options: list,
            ) -> None:
                self.requests.append(req_id)

            def cancelMktData(self, req_id: int) -> None:
                self.cancelled.append(req_id)

        class FakeWrapper:
            def __init__(self) -> None:
                self.started = 0

            def startTicker(self, req_id: int, contract: object, tick_type: str):
                self.started += 1
                if self.started == 1:
                    return SimpleNamespace(
                        req_id=req_id,
                        bid=None,
                        ask=None,
                        last=None,
                        marketDataType=1,
                        open=None,
                        high=None,
                        low=None,
                        close=None,
                        volume=None,
                    )
                return SimpleNamespace(
                    req_id=req_id,
                    bid=14.0,
                    ask=14.02,
                    last=14.01,
                    marketDataType=1,
                    open=None,
                    high=None,
                    low=None,
                    close=None,
                    volume=1000,
                )

            def endTicker(self, ticker: object, tick_type: str) -> int:
                return int(ticker.req_id)

        class FakeIb:
            def __init__(self) -> None:
                self.client = FakeClient()
                self.wrapper = FakeWrapper()

        connector = IbAsyncTwsConnector(
            "paper",
            {"market_data_type": 1, "live_quote_wait_seconds": 0.05},
        )
        connector._ib = FakeIb()

        first = asyncio.run(
            connector._ticker_market_snapshot_for_type(
                "NOK",
                SimpleNamespace(symbol="NOK"),
                timeout=1,
                market_data_type=1,
            )
        )
        second = asyncio.run(
            connector._ticker_market_snapshot_for_type(
                "NOK",
                SimpleNamespace(symbol="NOK"),
                timeout=1,
                market_data_type=1,
            )
        )

        self.assertFalse(first["available"])
        self.assertEqual(first["quote_state"], "EMPTY_QUOTE")
        self.assertTrue(second["available"])
        self.assertEqual(second["quote_state"], "READY")
        self.assertEqual(second["bid"], 14.0)
        self.assertEqual(second["ask"], 14.02)
        self.assertEqual(connector._ib.client.requests, [91, 92])
        self.assertEqual(connector._ib.client.cancelled, [91])

    def test_routine_audit_events_are_skipped_from_event_log(self) -> None:
        self.assertTrue(
            TradingEngine._should_skip_tws_audit_event({"request": "accountValues", "status": "OK"})
        )
        self.assertFalse(
            TradingEngine._should_skip_tws_audit_event(
                {"request": "reqTickersAsync", "status": "OK"}
            )
        )
        self.assertFalse(
            TradingEngine._should_skip_tws_audit_event({"request": "reqMktData", "status": "OK"})
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

    def test_historical_market_snapshot_uses_fresh_cache(self) -> None:
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

        class FakeIb:
            def __init__(self) -> None:
                self.calls = 0

            async def reqHistoricalDataAsync(self, *args, **kwargs):
                self.calls += 1
                return bars

        connector = IbAsyncTwsConnector(
            "paper",
            {
                "market_data_ttl": {
                    "live_quote_seconds": 20,
                    "atr_15m_seconds": 1200,
                    "atr_1h_seconds": 5400,
                    "historical_seconds": 300,
                }
            },
        )
        connector._ib = FakeIb()
        contract = SimpleNamespace(symbol="UEC")

        first = asyncio.run(
            connector._historical_market_snapshot(
                "UEC",
                contract,
                timeout=1,
                duration="5 D",
                bar_size="15 mins",
            )
        )
        second = asyncio.run(
            connector._historical_market_snapshot(
                "UEC",
                contract,
                timeout=1,
                duration="5 D",
                bar_size="15 mins",
            )
        )

        self.assertTrue(first["available"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["cache_status"], "HIT")
        self.assertEqual(connector._ib.calls, 1)

    def test_calculate_spread_validates_bid_ask(self) -> None:
        self.assertEqual(calculate_spread(15.13, 15.15), 0.02)
        self.assertIsNone(calculate_spread(None, 15.15))
        self.assertIsNone(calculate_spread(15.15, 15.13))

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

    def test_hybrid_market_snapshot_merges_signal_live_and_atr(self) -> None:
        snapshot = _merge_hybrid_market_snapshot(
            symbol="NOK",
            source="paper",
            signal={
                "available": True,
                "market_data_source": "historical",
                "price": 15.10,
                "open": 14.86,
                "high": 15.06,
                "low": 14.44,
                "close": 15.10,
                "volume_ratio": 0.76,
                "atr_15m": 0.22,
                "historical_bar_size": "15 mins",
                "bar_count": 120,
                "timestamp": _utc_now_iso(),
            },
            live={
                "available": True,
                "market_data_source": "live",
                "live_quote_source": "reqMktData",
                "market_data_type_requested": 1,
                "market_data_type_actual": 1,
                "price": 15.14,
                "bid": 15.13,
                "ask": 15.15,
                "last": 15.14,
                "timestamp": _utc_now_iso(),
            },
            atr_1h={
                "available": True,
                "market_data_source": "historical",
                "atr_1h": 0.48,
                "historical_bar_size": "1 hour",
                "bar_count": 80,
                "timestamp": _utc_now_iso(),
            },
        )

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["market_data_source"], "hybrid")
        self.assertEqual(snapshot["price"], 15.14)
        self.assertEqual(snapshot["close"], 15.10)
        self.assertEqual(snapshot["bid"], 15.13)
        self.assertEqual(snapshot["ask"], 15.15)
        self.assertEqual(snapshot["market_data_type_actual"], 1)
        self.assertEqual(snapshot["atr_15m"], 0.22)
        self.assertEqual(snapshot["atr_1h"], 0.48)
        self.assertEqual(snapshot["spread"], 0.02)
        self.assertEqual(snapshot["bars_15m_count"], 120)
        self.assertEqual(snapshot["bars_1h_count"], 80)
        self.assertEqual(snapshot["readiness"], "READY")
        self.assertEqual(snapshot["market_data_readiness"]["missing"], [])
        self.assertTrue(snapshot["market_data_readiness"]["order_submission_ready"])
        self.assertEqual(snapshot["hybrid_signal_bar_size"], "15 mins")

    def test_hybrid_market_snapshot_allows_delayed_quotes_for_paper_when_essentials_ready(
        self,
    ) -> None:
        snapshot = _merge_hybrid_market_snapshot(
            symbol="NOK",
            source="paper",
            signal={
                "available": True,
                "market_data_source": "historical",
                "price": 15.10,
                "open": 14.80,
                "high": 15.20,
                "low": 14.70,
                "close": 15.10,
                "volume": 125000,
                "atr_15m": 0.22,
                "bar_count": 120,
                "timestamp": _utc_now_iso(),
            },
            live={
                "available": True,
                "market_data_source": "live",
                "live_quote_source": "reqMktData",
                "market_data_type_requested": 3,
                "market_data_type_actual": 3,
                "price": 15.14,
                "bid": 15.13,
                "ask": 15.15,
                "last": 15.14,
                "timestamp": _utc_now_iso(),
            },
            atr_1h={
                "available": True,
                "market_data_source": "historical",
                "atr_1h": 0.48,
                "bar_count": 80,
                "timestamp": _utc_now_iso(),
            },
        )

        self.assertEqual(snapshot["bid"], 15.13)
        self.assertEqual(snapshot["ask"], 15.15)
        self.assertEqual(snapshot["market_data_type_actual"], 3)
        self.assertEqual(snapshot["live_market_data_status"], "DELAYED")
        self.assertEqual(snapshot["readiness"], "READY")
        self.assertEqual(
            snapshot["market_data_readiness"]["fields"]["live_market_data"]["status"],
            "WARNING",
        )
        self.assertEqual(snapshot["market_data_readiness"]["missing"], [])
        self.assertIn(
            "WARNING_NOT_LIVE_MARKET_DATA",
            snapshot["market_data_readiness"]["warnings"],
        )
        self.assertTrue(snapshot["market_data_readiness"]["order_submission_ready"])

    def test_hybrid_market_snapshot_blocks_delayed_quotes_for_live_orders(self) -> None:
        snapshot = _merge_hybrid_market_snapshot(
            symbol="NOK",
            source="live",
            signal={
                "available": True,
                "market_data_source": "historical",
                "price": 15.10,
                "open": 14.80,
                "high": 15.20,
                "low": 14.70,
                "close": 15.10,
                "volume": 125000,
                "atr_15m": 0.22,
                "bar_count": 120,
                "timestamp": _utc_now_iso(),
            },
            live={
                "available": True,
                "market_data_source": "live",
                "live_quote_source": "reqMktData",
                "market_data_type_requested": 1,
                "market_data_type_actual": 3,
                "price": 15.14,
                "bid": 15.13,
                "ask": 15.15,
                "last": 15.14,
                "timestamp": _utc_now_iso(),
            },
            atr_1h={
                "available": True,
                "market_data_source": "historical",
                "atr_1h": 0.48,
                "bar_count": 80,
                "timestamp": _utc_now_iso(),
            },
        )

        self.assertEqual(snapshot["live_market_data_status"], "DELAYED")
        self.assertEqual(snapshot["readiness"], "PAUSED_NOT_LIVE_MARKET_DATA")
        self.assertEqual(
            snapshot["market_data_readiness"]["fields"]["live_market_data"]["status"],
            "BLOCKED",
        )
        self.assertIn("live_market_data", snapshot["market_data_readiness"]["missing"])
        self.assertIn(
            "BLOCKED_NOT_LIVE_MARKET_DATA",
            snapshot["market_data_readiness"]["blocking_reasons"],
        )
        self.assertFalse(snapshot["market_data_readiness"]["order_submission_ready"])

    def test_hybrid_market_snapshot_separates_missing_indicator_from_live_data(self) -> None:
        snapshot = _merge_hybrid_market_snapshot(
            symbol="NOK",
            source="paper",
            signal={
                "available": True,
                "market_data_source": "historical",
                "price": 15.10,
                "open": 14.80,
                "high": 15.20,
                "low": 14.70,
                "close": 15.10,
                "volume": 125000,
                "atr_15m": 0.22,
                "bar_count": 120,
                "timestamp": _utc_now_iso(),
            },
            live={
                "available": True,
                "market_data_source": "live",
                "live_quote_source": "reqMktData",
                "market_data_type_requested": 3,
                "market_data_type_actual": 3,
                "live_market_data_status": "DELAYED",
                "price": 15.14,
                "bid": 15.13,
                "ask": 15.15,
                "last": 15.14,
                "timestamp": _utc_now_iso(),
            },
            atr_1h={
                "available": True,
                "market_data_source": "historical",
                "atr_1h": None,
                "atr_1h_status": "MISSING",
                "historical_bar_size": "1 hour",
                "bar_count": 10,
                "timestamp": _utc_now_iso(),
            },
        )

        self.assertEqual(snapshot["readiness"], "PAUSED_MISSING_INDICATOR_DATA")
        self.assertEqual(snapshot["market_data_readiness"]["missing"], ["atr_1h"])
        self.assertIn(
            "WARNING_NOT_LIVE_MARKET_DATA",
            snapshot["market_data_readiness"]["warnings"],
        )
        self.assertFalse(snapshot["market_data_readiness"]["warmup_ready"])
        self.assertTrue(snapshot["market_data_readiness"]["order_submission_ready"])

    def test_hybrid_market_snapshot_reports_combined_atr_and_live_missing(self) -> None:
        snapshot = _merge_hybrid_market_snapshot(
            symbol="NOK",
            source="live",
            signal={
                "available": True,
                "market_data_source": "historical",
                "price": 15.10,
                "open": 14.80,
                "high": 15.20,
                "low": 14.70,
                "close": 15.10,
                "volume": 125000,
                "atr_15m": 0.22,
                "bar_count": 120,
                "timestamp": _utc_now_iso(),
            },
            live={
                "available": True,
                "market_data_source": "live",
                "live_quote_source": "reqMktData",
                "market_data_type_requested": 3,
                "market_data_type_actual": 3,
                "live_market_data_status": "DELAYED",
                "price": 15.14,
                "bid": 15.13,
                "ask": 15.15,
                "last": 15.14,
                "timestamp": _utc_now_iso(),
            },
            atr_1h={
                "available": True,
                "market_data_source": "historical",
                "atr_1h": None,
                "atr_1h_status": "MISSING",
                "historical_bar_size": "1 hour",
                "bar_count": 0,
                "timestamp": _utc_now_iso(),
            },
        )

        self.assertEqual(snapshot["readiness"], "PAUSED_MISSING_MARKET_DATA")
        self.assertEqual(
            snapshot["market_data_readiness"]["missing"],
            ["live_market_data", "atr_1h"],
        )

    def test_atr_1h_uses_simple_average_of_last_14_true_ranges(self) -> None:
        bars = [
            SimpleNamespace(
                date=f"20260601 {hour:02d}:00:00",
                open=100 + hour,
                high=102 + hour,
                low=99 + hour,
                close=101 + hour,
                volume=1000,
            )
            for hour in range(15)
        ]

        snapshot = _historical_quote_from_bars(
            "NOK",
            "paper",
            bars,
            bar_size="1 hour",
        )

        self.assertEqual(snapshot["bar_count"], 15)
        self.assertEqual(snapshot["bars_required_for_atr"], 15)
        self.assertEqual(snapshot["atr_1h_status"], "OK")
        self.assertEqual(snapshot["atr_1h"], 3.0)

    def test_atr_1h_refresh_keeps_previous_valid_cache_when_new_bars_incomplete(self) -> None:
        class FakeIb:
            async def reqHistoricalDataAsync(self, *args):
                return [
                    SimpleNamespace(
                        date=f"20260602 {hour:02d}:00:00",
                        open=100 + hour,
                        high=101 + hour,
                        low=99 + hour,
                        close=100 + hour,
                        volume=1000,
                    )
                    for hour in range(10)
                ]

        connector = IbAsyncTwsConnector("paper", {})
        connector._ib = FakeIb()
        cache_key = connector._historical_cache_key("NOK", "30 D", "1 hour")
        old_timestamp = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        connector._historical_cache[cache_key] = {
            "updated_at": old_timestamp,
            "snapshot": {
                "available": True,
                "source": "paper",
                "market_data_source": "historical",
                "symbol": "NOK",
                "price": 100.0,
                "atr_1h": 0.48,
                "atr_1h_status": "OK",
                "bar_count": 80,
                "historical_bar_size": "1 hour",
                "historical_duration": "30 D",
                "timestamp": old_timestamp,
            },
        }

        snapshot = asyncio.run(
            connector._historical_market_snapshot(
                "NOK",
                object(),
                timeout=1.0,
                duration="30 D",
                bar_size="1 hour",
            )
        )

        self.assertEqual(snapshot["atr_1h"], 0.48)
        self.assertEqual(snapshot["atr_1h_status"], "STALE")
        self.assertEqual(snapshot["cache_status"], "STALE_FALLBACK")
        self.assertEqual(snapshot["last_successful_atr_1h"], 0.48)


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
        self.engine._heartbeat.assert_awaited_once_with(poll_stocks=False)
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
            self.engine._health["last_heartbeat_started_at"],
            "2026-06-04T10:00:01+00:00",
        )
        self.assertEqual(
            self.engine._health["last_heartbeat_completed_at"],
            "2026-06-04T10:00:30+00:00",
        )
        self.assertFalse(self.engine._health["heartbeat_in_progress"])
        self.assertEqual(
            self.engine._health["last_broker_check_at"],
            "2026-06-04T10:00:00+00:00",
        )

    async def test_stock_poll_timeout_does_not_leave_heartbeat_stale(self) -> None:
        class HangingQuoteBroker:
            connector_name = "paper"
            account_mode = "paper"
            display_name = "Test broker"
            host = "127.0.0.1"
            port = 7497
            client_id = 1001
            last_error = ""

            async def health_check(self) -> ConnectionStatus:
                return ConnectionStatus.CONNECTED

            async def status(self) -> ConnectionStatus:
                return ConnectionStatus.CONNECTED

            async def positions(self) -> list:
                return []

            async def open_orders(self) -> list:
                return []

            async def order_statuses(self) -> dict[str, str]:
                return {}

            async def market_snapshot(self, symbol: str, timeout: float = 4) -> dict:
                await asyncio.sleep(10)
                return {"available": False, "symbol": symbol}

            def diagnostics(self) -> dict:
                return {}

            def drain_audit_entries(self) -> list:
                return []

        self.settings.raw["market"]["tws_stock_poll_total_timeout_seconds"] = 0.1
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record())
        self.engine.broker = HangingQuoteBroker()
        self.engine.order_manager.broker = self.engine.broker
        self.engine.reconciliation.broker = self.engine.broker

        started = time.perf_counter()
        await self.engine._heartbeat()

        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(self.engine._health["last_stock_poll_reason"], "timeout")
        self.assertEqual(self.engine._health["last_stock_poll_timeout_seconds"], 0.1)
        self.assertFalse(self.engine._health["heartbeat_in_progress"])
        self.assertIsNotNone(self.engine._health["last_heartbeat_completed_at"])
        timeout_events = self.repository.list_events(
            event_type="stock_poll_timeout",
            limit=1,
        )
        self.assertEqual(len(timeout_events), 1)

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
        self.assertEqual(broker.calls[0]["timeout"], 15)

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
        analysis_event = next(event for event in events if event["event_type"] == "stock_analysis")
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
        self.assertTrue(any(check["label"] == "Breakout journalier" for check in trace["checks"]))

    async def test_repeated_missing_market_data_hold_is_deduplicated(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record())
        snapshot = MarketSnapshot(
            symbol="NOK",
            price=15.10,
            close=15.10,
        )

        first = await self.engine._analyze_market_snapshot(snapshot)
        second = await self.engine._analyze_market_snapshot(snapshot)

        self.assertEqual(first[0]["action"], "HOLD")
        self.assertEqual(second[0]["action"], "HOLD")
        self.assertIn("PAUSED_MISSING_MARKET_DATA", first[0]["reason"])
        events = [
            event
            for event in self.repository.list_events(symbol="NOK", limit=10)
            if event["event_type"] == "stock_analysis"
        ]
        self.assertEqual(len(events), 1)

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
        analysis_event = next(event for event in events if event["event_type"] == "stock_analysis")
        trace = analysis_event["data"]["processed"][0]["trace"]
        labels = [check["label"] for check in trace["checks"]]
        self.assertIn("Transmission ask <= limite", labels)
        self.assertIn("Setup depasse", labels)


if __name__ == "__main__":
    unittest.main()
