from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from app.api import routes_orders
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.manual_order_service import (
    REASON_MANUAL_SELL_EXCEEDS_POSITION,
    REASON_MANUAL_STOP_REQUIRED,
    ManualOrderService,
)
from app.engine.order_manager import OrderManager
from app.engine.risk_engine import RiskLimits
from app.engine.trade_guards import (
    REASON_HALT_ACTIVE,
    REASON_OUTSIDE_TRADING_WINDOW,
    TradeGuardsService,
)
from app.models import PositionRecord
from app.settings import DEFAULT_CONFIG
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

RTH_OPEN_WINDOW = datetime(2026, 7, 7, 15, 0, tzinfo=UTC)  # Tuesday 11:00 New York
RTH_BEFORE_WINDOW = datetime(2026, 7, 7, 13, 45, tzinfo=UTC)  # Tuesday 09:45 New York


class ManualOrderServiceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()
        self.settings = deepcopy(DEFAULT_CONFIG)
        self.guards = TradeGuardsService(self.repository, self.settings)
        self.order_manager = OrderManager(
            self.repository,
            self.event_store,
            self.broker,
            settings=self.settings,
        )
        self.now = RTH_OPEN_WINDOW
        self.service = ManualOrderService(
            self.repository,
            self.event_store,
            self.order_manager,
            self.guards,
            RiskLimits.from_config(self.settings),
            self.settings,
            broker=self.broker,
            market_snapshot_provider=lambda symbol: SimpleNamespace(price=20.0, spread=0.02),
            account_summary_reader=self._account_summary,
            current_time_provider=lambda: self.now,
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def _account_summary(self) -> dict[str, Any]:
        return {"net_liquidation": 1000.0}

    def _buy_payload(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": "LUNR",
            "side": "BUY",
            "quantity": 5,
            "order_type": "LMT",
            "limit_price": 20.0,
            "trigger_price": None,
            "stop_loss": 18.0,
            "allow_unprotected": False,
        }
        payload.update(overrides)
        return payload

    def _manual_traces(self) -> list[dict[str, Any]]:
        return self.repository.list_decision_traces(decision_type="MANUAL_ORDER")


class ManualBuyOrderTests(ManualOrderServiceTestCase):
    async def test_buy_without_stop_is_a_validation_error(self) -> None:
        result = await self.service.submit(self._buy_payload(stop_loss=None))

        self.assertFalse(result["ok"])
        assert result["validation_error"] is not None
        self.assertEqual(
            result["validation_error"]["reason_code"],
            REASON_MANUAL_STOP_REQUIRED,
        )
        self.assertEqual(await self.broker.open_orders(), [])
        traces = self._manual_traces()
        self.assertEqual(len(traces), 1)
        self.assertIn(REASON_MANUAL_STOP_REQUIRED, traces[0]["final_decision"])

    async def test_valid_buy_creates_protected_bracket_visible_in_snapshot(self) -> None:
        result = await self.service.submit(self._buy_payload())

        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["order_id"])
        self.assertIsNotNone(result["stop_order_id"])
        entry = self.repository.get_order(result["order_id"])
        assert entry is not None
        self.assertTrue(str(entry["setup_id"]).startswith("man_"))
        self.assertEqual(entry["symbol"], "LUNR")
        stop = self.repository.get_order(result["stop_order_id"])
        assert stop is not None
        self.assertEqual(stop["side"], "SELL")
        self.assertEqual(stop["stop_price"], 18.0)
        broker_orders = await self.broker.open_orders()
        self.assertEqual(len(broker_orders), 2)
        traces = self._manual_traces()
        self.assertEqual(traces[0]["final_decision"], "GO:MANUAL_ORDER_SUBMITTED")

    async def test_preview_risk_matches_server_computation(self) -> None:
        result = await self.service.preview(self._buy_payload())

        self.assertTrue(result["ok"])
        risk = result["risk"]
        self.assertEqual(risk["reference_entry_price"], 20.0)
        self.assertEqual(risk["risk_per_share"], 2.0)
        self.assertEqual(risk["risk_usd"], 10.0)  # 5 shares x 2.0
        self.assertEqual(risk["risk_pct_of_account"], 1.0)  # 10 / 1000
        self.assertEqual(risk["position_amount_usd"], 100.0)
        # Preview never submits or traces anything.
        self.assertEqual(await self.broker.open_orders(), [])
        self.assertEqual(self._manual_traces(), [])

    async def test_halted_symbol_is_refused_and_traced(self) -> None:
        self.guards.set_halt_state("LUNR", halted=True)

        result = await self.service.submit(self._buy_payload())

        self.assertFalse(result["ok"])
        assert result["block"] is not None
        self.assertEqual(result["block"]["reason_code"], REASON_HALT_ACTIVE)
        self.assertEqual(await self.broker.open_orders(), [])
        traces = self._manual_traces()
        self.assertEqual(len(traces), 1)
        self.assertIn(REASON_HALT_ACTIVE, traces[0]["final_decision"])
        self.assertEqual(traces[0]["trace"]["payload"]["symbol"], "LUNR")

    async def test_outside_trading_window_is_refused(self) -> None:
        self.now = RTH_BEFORE_WINDOW

        result = await self.service.submit(self._buy_payload())

        self.assertFalse(result["ok"])
        assert result["block"] is not None
        self.assertEqual(result["block"]["reason_code"], REASON_OUTSIDE_TRADING_WINDOW)
        self.assertEqual(await self.broker.open_orders(), [])

    async def test_risk_above_limit_is_refused(self) -> None:
        # 50 shares x 2.0 risk per share = 100 USD > max_risk_per_trade_usd.
        result = await self.service.submit(self._buy_payload(quantity=50))

        self.assertFalse(result["ok"])
        assert result["block"] is not None
        self.assertEqual(result["block"]["source"], "risk_limits")
        self.assertEqual(await self.broker.open_orders(), [])

    async def test_stop_above_entry_is_a_validation_error(self) -> None:
        result = await self.service.submit(self._buy_payload(stop_loss=21.0))

        self.assertFalse(result["ok"])
        assert result["validation_error"] is not None

    async def test_unprotected_buy_allowed_only_on_simulated_connector(self) -> None:
        result = await self.service.submit(
            self._buy_payload(stop_loss=None, allow_unprotected=True)
        )

        self.assertTrue(result["ok"])
        self.assertIsNone(result.get("stop_order_id"))
        broker_orders = await self.broker.open_orders()
        self.assertEqual(len(broker_orders), 1)


class ManualSellOrderTests(ManualOrderServiceTestCase):
    def _seed_position(self, quantity: int = 6) -> None:
        self.repository.upsert_position(
            PositionRecord(
                symbol="LUNR",
                setup_id="LUNR_SETUP",
                quantity=quantity,
                average_price=20.0,
                current_price=21.0,
                unrealized_pnl=6.0,
                current_stop=18.0,
                risk_remaining=12.0,
                status="OPEN",
            )
        )

    async def test_sell_is_reduce_only(self) -> None:
        self._seed_position(quantity=6)

        result = await self.service.submit(
            self._buy_payload(side="SELL", quantity=10, order_type="MKT", stop_loss=None)
        )

        self.assertFalse(result["ok"])
        assert result["block"] is not None
        self.assertEqual(
            result["block"]["reason_code"],
            REASON_MANUAL_SELL_EXCEEDS_POSITION,
        )

    async def test_sell_without_position_is_refused(self) -> None:
        result = await self.service.submit(
            self._buy_payload(side="SELL", quantity=1, order_type="MKT", stop_loss=None)
        )

        self.assertFalse(result["ok"])

    async def test_partial_sell_of_open_position_is_submitted(self) -> None:
        self._seed_position(quantity=6)

        result = await self.service.submit(
            self._buy_payload(side="SELL", quantity=4, order_type="MKT", stop_loss=None)
        )

        self.assertTrue(result["ok"])
        order = self.repository.get_order(result["order_id"])
        assert order is not None
        self.assertEqual(order["side"], "SELL")
        self.assertEqual(order["quantity"], 4)
        broker_orders = await self.broker.open_orders()
        self.assertEqual(len(broker_orders), 1)


class ManualOrderRouteTests(ManualOrderServiceTestCase):
    def _request(self) -> SimpleNamespace:
        async def _broadcast() -> None:
            return None

        engine = SimpleNamespace(
            manual_order_service=self.service,
            _broadcast_snapshot=_broadcast,
        )
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))

    async def test_buy_without_stop_returns_400(self) -> None:
        payload = routes_orders.ManualOrderPayload(
            symbol="LUNR",
            side="BUY",
            quantity=5,
            order_type="LMT",
            limit_price=20.0,
        )

        with self.assertRaises(HTTPException) as ctx:
            await routes_orders.manual_order(self._request(), payload)

        self.assertEqual(ctx.exception.status_code, 400)

    async def test_guard_block_returns_422(self) -> None:
        self.guards.set_halt_state("LUNR", halted=True)
        payload = routes_orders.ManualOrderPayload(
            symbol="LUNR",
            side="BUY",
            quantity=5,
            order_type="LMT",
            limit_price=20.0,
            stop_loss=18.0,
        )

        with self.assertRaises(HTTPException) as ctx:
            await routes_orders.manual_order(self._request(), payload)

        self.assertEqual(ctx.exception.status_code, 422)

    async def test_valid_order_returns_result(self) -> None:
        payload = routes_orders.ManualOrderPayload(
            symbol="LUNR",
            side="BUY",
            quantity=5,
            order_type="LMT",
            limit_price=20.0,
            stop_loss=18.0,
        )

        result = await routes_orders.manual_order(self._request(), payload)

        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["order_id"])


if __name__ == "__main__":
    unittest.main()
