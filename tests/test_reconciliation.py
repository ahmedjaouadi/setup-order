from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerExecution
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.broker_reality import REPORT_STATE_KEY
from app.engine.reconciliation import ReconciliationEngine, _match_executions_to_order
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


def _order(**overrides) -> dict:
    base = {
        "id": "ord_1",
        "setup_id": "SETUP_1",
        "symbol": "TEST",
        "side": "BUY",
        "quantity": 40,
        "broker_order_id": "9001",
        "broker_perm_id": "555",
    }
    base.update(overrides)
    return base


def _execution(**overrides) -> BrokerExecution:
    base = dict(
        execution_id="EXEC_1",
        symbol="TEST",
        side="BUY",
        quantity=10,
        price=100.0,
        order_id="9001",
        broker_perm_id="555",
        timestamp="2026-07-23T10:00:00Z",
    )
    base.update(overrides)
    return BrokerExecution(**base)


class MatchExecutionsToOrderTests(unittest.TestCase):
    def test_matches_by_order_id_alone(self) -> None:
        order = _order(broker_perm_id=None)
        execution = _execution(order_id="9001", broker_perm_id=None)

        match = _match_executions_to_order([execution], order)

        self.assertIsNotNone(match)
        self.assertEqual(match["execution_count"], 1)
        self.assertEqual(match["quantity"], 10)

    def test_matches_by_broker_perm_id_alone(self) -> None:
        order = _order(broker_order_id=None)
        execution = _execution(order_id=None, broker_perm_id="555")

        match = _match_executions_to_order([execution], order)

        self.assertIsNotNone(match)
        self.assertEqual(match["execution_count"], 1)

    def test_no_match_when_identifiers_empty_on_one_or_both_sides(self) -> None:
        # Execution has identifiers, but the local order has none: a present
        # identifier on one side must never match an absent one.
        order_without_ids = _order(broker_order_id=None, broker_perm_id=None)
        execution_with_ids = _execution(order_id="9001", broker_perm_id="555")
        self.assertIsNone(_match_executions_to_order([execution_with_ids], order_without_ids))

        # Execution has no identifiers at all, order has valid ones: still no match.
        order_with_ids = _order()
        execution_without_ids = _execution(order_id=None, broker_perm_id=None)
        self.assertIsNone(_match_executions_to_order([execution_without_ids], order_with_ids))

        # Both sides empty for both identifiers: two Nones never pair up.
        self.assertIsNone(
            _match_executions_to_order([execution_without_ids], order_without_ids)
        )

    def test_multiple_executions_are_summed_with_weighted_average_price(self) -> None:
        order = _order(quantity=40)
        executions = [
            _execution(execution_id="E1", quantity=10, price=100.0),
            _execution(execution_id="E2", quantity=30, price=110.0),
        ]

        match = _match_executions_to_order(executions, order)

        self.assertIsNotNone(match)
        self.assertEqual(match["execution_count"], 2)
        self.assertEqual(match["quantity"], 40)
        simple_average = (100.0 + 110.0) / 2
        weighted_average = (10 * 100.0 + 30 * 110.0) / 40
        self.assertNotEqual(simple_average, weighted_average)
        self.assertAlmostEqual(match["price"], weighted_average)
        self.assertTrue(match["quantity_matches"])

    def test_executions_of_other_orders_are_ignored(self) -> None:
        order = _order()
        other_order_execution = _execution(
            execution_id="E_OTHER", order_id="9999", broker_perm_id="777"
        )

        match = _match_executions_to_order([other_order_execution], order)

        self.assertIsNone(match)

    def test_executions_with_opposite_side_are_ignored(self) -> None:
        order = _order(side="BUY")
        opposite_side_execution = _execution(side="SELL")

        match = _match_executions_to_order([opposite_side_execution], order)

        self.assertIsNone(match)

    def test_empty_execution_list_returns_none(self) -> None:
        self.assertIsNone(_match_executions_to_order([], _order()))

    def test_quantity_matches_flag_reflects_totals(self) -> None:
        matching_order = _order(quantity=10)
        execution = _execution(quantity=10)
        match = _match_executions_to_order([execution], matching_order)
        self.assertIsNotNone(match)
        self.assertTrue(match["quantity_matches"])

        mismatched_order = _order(quantity=999)
        match = _match_executions_to_order([execution], mismatched_order)
        self.assertIsNotNone(match)
        self.assertFalse(match["quantity_matches"])


if __name__ == "__main__":
    unittest.main()
