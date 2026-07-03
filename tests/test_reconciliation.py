from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.broker_reality import REPORT_STATE_KEY
from app.engine.reconciliation import ReconciliationEngine
from app.models import OrderRecord, OrderStatus
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class FailingOpenOrdersBroker(SimulatedBrokerConnector):
    async def open_orders(self):
        raise RuntimeError("TWS timeout on reqOpenOrders")


class FailingPositionsBroker(SimulatedBrokerConnector):
    async def positions(self):
        raise RuntimeError("TWS timeout on reqPositions")


class ReconciliationPartialFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_open_orders_query_error_is_not_empty_ok(self) -> None:
        broker = FailingOpenOrdersBroker()
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)

        result = await reconciliation.run()

        self.assertTrue(result["auto_execution_blocked"])
        report = self.repository.get_bot_state(REPORT_STATE_KEY, {})
        self.assertIn(report["channels"]["orders"]["status"], {"ERROR", "PARTIAL"})
        self.assertIn("BROKER_ORDERS_QUERY_FAILED", report["blocking_reasons"])
        self.assertIsNone(report["broker_active_orders"])

    async def test_positions_query_error_is_not_empty_ok(self) -> None:
        broker = FailingPositionsBroker()
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)

        result = await reconciliation.run()

        self.assertTrue(result["auto_execution_blocked"])
        report = self.repository.get_bot_state(REPORT_STATE_KEY, {})
        self.assertIn(report["channels"]["positions"]["status"], {"ERROR", "PARTIAL"})
        self.assertIn("BROKER_POSITIONS_QUERY_FAILED", report["blocking_reasons"])
        self.assertIsNone(report["broker_positions_count"])

    async def test_positions_query_error_does_not_wrongly_cancel_local_orders(self) -> None:
        self.repository.upsert_order(
            OrderRecord(
                id="ord_active",
                setup_id="SETUP_1",
                symbol="TEST",
                side="BUY",
                order_type="STP_LMT",
                quantity=10,
                status=OrderStatus.SUBMITTED.value,
                broker_order_id="9001",
            )
        )
        broker = FailingPositionsBroker()
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)

        await reconciliation.run()

        order = self.repository.get_order("ord_active")
        self.assertEqual(order["status"], OrderStatus.SUBMITTED.value)


if __name__ == "__main__":
    unittest.main()
