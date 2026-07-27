from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.broker.ib_models import BrokerExecution, BrokerPosition
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.broker_reality import REPORT_STATE_KEY
from app.engine.position_manager import PositionManager
from app.engine.reconciliation import ReconciliationEngine, _match_executions_to_order
from app.models import OrderRecord, OrderStatus, OrderType, PositionRecord, SetupStatus
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


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


class FilledBranchTests(unittest.TestCase):
    """Lot 3b-2: FILLED branch of _update_setup_after_reconciled_order."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.reconciliation = ReconciliationEngine(
            self.repository, self.event_store, SimulatedBrokerConnector()
        )
        self.config = valid_breakout_config()
        self.symbol = self.config["symbol"]
        self.repository.upsert_setup(BreakoutRetestSetup(self.config).to_record())
        self.order = OrderRecord(
            id="ord-1",
            setup_id=self.config["setup_id"],
            symbol=self.symbol,
            side="BUY",
            order_type=OrderType.STP_LMT.value,
            quantity=40,
            status=OrderStatus.SUBMITTED.value,
            broker_order_id="9001",
            broker_perm_id="555",
        )
        self.repository.upsert_order(self.order)
        self.repository.update_setup_status(
            self.config["setup_id"], SetupStatus.ENTRY_ORDER_PLACED.value, "test setup"
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _order_dict(self, **overrides) -> dict:
        base = {
            "id": self.order.id,
            "setup_id": self.config["setup_id"],
            "symbol": self.symbol,
            "side": "BUY",
            "quantity": 40,
            "broker_order_id": "9001",
            "broker_perm_id": "555",
        }
        base.update(overrides)
        return base

    def _add_active_stop_order(self) -> None:
        self.repository.upsert_order(
            OrderRecord(
                id="stp-1",
                setup_id=self.config["setup_id"],
                symbol=self.symbol,
                side="SELL",
                order_type=OrderType.STP.value,
                quantity=40,
                status=OrderStatus.SUBMITTED.value,
                stop_price=13.85,
                parent_id=self.order.id,
            )
        )

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.config["setup_id"])["status"])

    def _event_types(self) -> set[str]:
        return {event["event_type"] for event in self.repository.list_events(limit=20)}

    def test_barreau1_nominal_weighted_price_reaches_in_position(self) -> None:
        self._add_active_stop_order()
        executions = [
            _execution(
                execution_id="E1", quantity=10, price=100.0, order_id="9001", broker_perm_id="555"
            ),
            _execution(
                execution_id="E2", quantity=30, price=110.0, order_id="9001", broker_perm_id="555"
            ),
        ]

        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
        position = self.repository.get_position(self.symbol)
        weighted_average = (10 * 100.0 + 30 * 110.0) / 40
        self.assertNotEqual(weighted_average, (100.0 + 110.0) / 2)
        self.assertAlmostEqual(position["average_price"], weighted_average)
        self.assertEqual(position["quantity"], 40)

    def test_barreau1_without_active_stop_requires_manual_review(self) -> None:
        # The 2026-06-29 incident: entry filled while its stop was rejected.
        executions = [
            _execution(order_id="9001", broker_perm_id="555", quantity=40, price=100.0)
        ]

        with mock.patch.object(
            self.reconciliation.progression,
            "mark_in_position",
            wraps=self.reconciliation.progression.mark_in_position,
        ) as mark_in_position:
            self.reconciliation._update_setup_after_reconciled_order(
                self._order_dict(),
                OrderStatus.FILLED.value,
                broker_positions=[],
                broker_executions=executions,
            )
            mark_in_position.assert_not_called()

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("entry_filled_without_protection", self._event_types())

    def test_barreau1_quantity_mismatch_falls_through_to_barreau3(self) -> None:
        executions = [
            _execution(order_id="9001", broker_perm_id="555", quantity=10, price=100.0)
        ]

        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(quantity=40),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIsNone(self.repository.get_position(self.symbol))
        self.assertIn("entry_filled_unknown_fill_details", self._event_types())

    def test_barreau2_used_when_position_newly_born(self) -> None:
        self._add_active_stop_order()
        broker_position = BrokerPosition(
            symbol=self.symbol, quantity=40, average_price=105.0, current_price=106.0
        )

        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(),
            OrderStatus.FILLED.value,
            broker_positions=[broker_position],
            broker_executions=[],
        )

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
        position = self.repository.get_position(self.symbol)
        self.assertEqual(position["average_price"], 105.0)
        self.assertEqual(position["quantity"], 40)

    def test_barreau2_excluded_when_local_position_preexists(self) -> None:
        self.repository.upsert_position(
            PositionRecord(
                symbol=self.symbol,
                setup_id="OTHER_SETUP",
                quantity=5,
                average_price=50.0,
                current_price=51.0,
                unrealized_pnl=5.0,
                current_stop=45.0,
                risk_remaining=25.0,
                status="OPEN",
            )
        )
        broker_position = BrokerPosition(
            symbol=self.symbol, quantity=40, average_price=105.0, current_price=106.0
        )

        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(),
            OrderStatus.FILLED.value,
            broker_positions=[broker_position],
            broker_executions=[],
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

    def test_barreau3_no_reliable_source_marks_manual_review_without_position(self) -> None:
        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=[],
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIsNone(self.repository.get_position(self.symbol))
        self.assertIn("entry_filled_unknown_fill_details", self._event_types())

    def test_sell_filled_without_resolvable_fill_goes_to_manual_review(self) -> None:
        # A-1 (root cause A / T1): a SELL fill is no longer ignored outright.
        # With no matching executions to resolve quantity/price from, it must
        # alert instead of silently doing nothing (the old, now-removed
        # `if side != "BUY": return` behaviour).
        with mock.patch.object(
            self.reconciliation.progression,
            "record_fill",
            wraps=self.reconciliation.progression.record_fill,
        ) as record_fill:
            self.reconciliation._update_setup_after_reconciled_order(
                self._order_dict(side="SELL"),
                OrderStatus.FILLED.value,
                broker_positions=[],
                broker_executions=[],
            )
            record_fill.assert_not_called()

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIsNone(self.repository.get_position(self.symbol))
        self.assertIn("sell_filled_unknown_fill_details", self._event_types())

    def test_setup_already_in_position_receives_no_write(self) -> None:
        self.repository.update_setup_status(
            self.config["setup_id"], SetupStatus.IN_POSITION.value, "already in position"
        )
        executions = [
            _execution(order_id="9001", broker_perm_id="555", quantity=40, price=100.0)
        ]

        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
        self.assertIsNone(self.repository.get_position(self.symbol))

    def test_setup_status_guard_prevents_second_record_fill(self) -> None:
        self._add_active_stop_order()
        executions = [
            _execution(order_id="9001", broker_perm_id="555", quantity=40, price=100.0)
        ]

        with mock.patch.object(
            self.reconciliation.progression,
            "record_fill",
            wraps=self.reconciliation.progression.record_fill,
        ) as record_fill:
            self.reconciliation._update_setup_after_reconciled_order(
                self._order_dict(),
                OrderStatus.FILLED.value,
                broker_positions=[],
                broker_executions=executions,
            )
            self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)

            # Second reconciliation pass for the same order: the setup is no
            # longer ENTRY_ORDER_PLACED/ENTRY_PARTIALLY_FILLED, so the status
            # guard blocks re-entry into the FILLED branch.
            self.reconciliation._update_setup_after_reconciled_order(
                self._order_dict(),
                OrderStatus.FILLED.value,
                broker_positions=[],
                broker_executions=executions,
            )
            record_fill.assert_called_once()


class SellFilledBranchTests(unittest.TestCase):
    """A-1: FILLED branch handling of a real SELL fill (root cause A, T1).

    Before this lot, `_update_setup_after_reconciled_order` ignored every
    SELL fill (`if side != "BUY": return`), so a stop or manual exit fill
    never closed the local position nor fed the circuit breaker."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.closed_calls: list[tuple[str, float]] = []
        self.position_manager = PositionManager(
            self.repository,
            self.event_store,
            on_position_closed=lambda symbol, pnl: self.closed_calls.append((symbol, pnl)),
        )
        self.reconciliation = ReconciliationEngine(
            self.repository,
            self.event_store,
            SimulatedBrokerConnector(),
            position_manager=self.position_manager,
        )
        self.config = valid_breakout_config()
        self.symbol = self.config["symbol"]
        self.setup_id = self.config["setup_id"]
        self.repository.upsert_setup(BreakoutRetestSetup(self.config).to_record())
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.IN_POSITION.value, "test setup"
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _seed_position(
        self, quantity: int, average_price: float, current_stop: float | None = None
    ) -> None:
        self.repository.upsert_position(
            PositionRecord(
                symbol=self.symbol,
                setup_id=self.setup_id,
                quantity=quantity,
                average_price=average_price,
                current_price=average_price,
                unrealized_pnl=0.0,
                current_stop=current_stop,
                risk_remaining=0.0,
                status="OPEN",
            )
        )

    def _sell_order(self, **overrides) -> dict:
        base = {
            "id": "ord-sell-1",
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "side": "SELL",
            "quantity": 10,
            "broker_order_id": "9002",
            "broker_perm_id": "556",
        }
        base.update(overrides)
        return base

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def _event_types(self) -> set[str]:
        return {event["event_type"] for event in self.repository.list_events(limit=20)}

    def test_total_sell_closes_position(self) -> None:
        self._seed_position(quantity=10, average_price=100.0, current_stop=95.0)
        executions = [
            _execution(
                execution_id="ES1",
                side="SELL",
                quantity=10,
                price=90.0,
                order_id="9002",
                broker_perm_id="556",
            )
        ]

        self.reconciliation._update_setup_after_reconciled_order(
            self._sell_order(quantity=10),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )

        self.assertEqual(self._setup_status(), SetupStatus.CLOSED.value)
        position = self.repository.get_position(self.symbol)
        self.assertEqual(position["quantity"], 0)
        self.assertIn("position_closed_on_sell", self._event_types())

    def test_partial_sell_sets_partial_exit_and_keeps_entry_cost(self) -> None:
        self._seed_position(quantity=40, average_price=100.0, current_stop=95.0)
        executions = [
            _execution(
                execution_id="ES2",
                side="SELL",
                quantity=10,
                price=110.0,
                order_id="9002",
                broker_perm_id="556",
            )
        ]

        self.reconciliation._update_setup_after_reconciled_order(
            self._sell_order(quantity=10),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )

        self.assertEqual(self._setup_status(), SetupStatus.PARTIAL_EXIT.value)
        position = self.repository.get_position(self.symbol)
        self.assertEqual(position["quantity"], 30)
        # average_price must stay the original entry cost, never the sell price.
        self.assertEqual(position["average_price"], 100.0)

    def test_realized_pnl_uses_real_sell_price_not_generic_quote(self) -> None:
        # audit 62 Q3: current_price fed to the circuit breaker must be the
        # real fill price. Entry 100, sell 90, qty 10 -> pnl == -100.0 exactly.
        self._seed_position(quantity=10, average_price=100.0, current_stop=95.0)
        executions = [
            _execution(
                execution_id="ES3",
                side="SELL",
                quantity=10,
                price=90.0,
                order_id="9002",
                broker_perm_id="556",
            )
        ]

        self.reconciliation._update_setup_after_reconciled_order(
            self._sell_order(quantity=10),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )

        self.assertEqual(self.closed_calls, [(self.symbol, -100.0)])

    def test_broker_position_mismatch_triggers_manual_review(self) -> None:
        self._seed_position(quantity=40, average_price=100.0, current_stop=95.0)
        executions = [
            _execution(
                execution_id="ES4",
                side="SELL",
                quantity=10,
                price=110.0,
                order_id="9002",
                broker_perm_id="556",
            )
        ]
        # Local computation says 30 remain (40 - 10), but the broker reports 20.
        broker_position = BrokerPosition(
            symbol=self.symbol, quantity=20, average_price=100.0, current_price=110.0
        )

        self.reconciliation._update_setup_after_reconciled_order(
            self._sell_order(quantity=10),
            OrderStatus.FILLED.value,
            broker_positions=[broker_position],
            broker_executions=executions,
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("sell_filled_broker_quantity_mismatch", self._event_types())
        position = self.repository.get_position(self.symbol)
        self.assertEqual(position["quantity"], 40)
        self.assertEqual(self.closed_calls, [])

    def test_sell_resolved_but_no_local_position_triggers_manual_review(self) -> None:
        # Fill details resolve fine, but there is no known local position to
        # sell out of: a sale without a known position is an incoherence,
        # not a closure.
        executions = [
            _execution(
                execution_id="ES5",
                side="SELL",
                quantity=10,
                price=90.0,
                order_id="9002",
                broker_perm_id="556",
            )
        ]

        self.reconciliation._update_setup_after_reconciled_order(
            self._sell_order(quantity=10),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("sell_filled_no_local_position", self._event_types())
        self.assertIsNone(self.repository.get_position(self.symbol))
        self.assertEqual(self.closed_calls, [])

    def test_unresolved_executions_trigger_manual_review(self) -> None:
        self._seed_position(quantity=10, average_price=100.0, current_stop=95.0)

        self.reconciliation._update_setup_after_reconciled_order(
            self._sell_order(quantity=10),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=[],
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("sell_filled_unknown_fill_details", self._event_types())
        position = self.repository.get_position(self.symbol)
        self.assertEqual(position["quantity"], 10)
        self.assertEqual(self.closed_calls, [])


class SubmittedBranchReviewLockTests(unittest.TestCase):
    """S5b-1: the SUBMITTED (order-restore) branch must never overwrite a
    MANUAL_REVIEW_REQUIRED / ERROR_REQUIRES_MANUAL_REVIEW setup — these
    statuses mean "a human must look", and the 2026-06-29 incident (audits
    35/36) showed this branch silently erasing that alarm."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.reconciliation = ReconciliationEngine(
            self.repository, self.event_store, SimulatedBrokerConnector()
        )
        self.config = valid_breakout_config()
        self.symbol = self.config["symbol"]
        self.setup_id = self.config["setup_id"]
        self.repository.upsert_setup(BreakoutRetestSetup(self.config).to_record())

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def _event_types(self) -> set[str]:
        return {event["event_type"] for event in self.repository.list_events(limit=20)}

    def test_manual_review_required_survives_sell_order_reported_submitted(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )

        self.reconciliation._update_setup_after_reconciled_order(
            _order(setup_id=self.setup_id, symbol=self.symbol, side="SELL"),
            OrderStatus.SUBMITTED.value,
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("reconciliation_skipped_review_locked", self._event_types())

    def test_error_requires_manual_review_survives_buy_order_reported_submitted(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value, "test setup"
        )

        self.reconciliation._update_setup_after_reconciled_order(
            _order(setup_id=self.setup_id, symbol=self.symbol, side="BUY"),
            OrderStatus.SUBMITTED.value,
        )

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )
        self.assertIn("reconciliation_skipped_review_locked", self._event_types())

    def test_terminal_status_with_open_sell_order_goes_to_manual_review(self) -> None:
        # A6-SEC (audits 49/50): a terminal setup with a still-open broker
        # order must never be "resurrected" into an active order status —
        # it must alert a human instead. Superseded the old behaviour
        # (restoring STOP_ORDER_PLACED/ENTRY_ORDER_PLACED here), which was
        # the finding this lot fixes.
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.CANCELLED.value, "test setup"
        )

        with mock.patch.object(
            self.reconciliation.broker, "cancel_order", new=mock.AsyncMock()
        ) as cancel_order:
            self.reconciliation._update_setup_after_reconciled_order(
                _order(setup_id=self.setup_id, symbol=self.symbol, side="SELL"),
                OrderStatus.SUBMITTED.value,
            )
            cancel_order.assert_not_called()

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("reconciliation_terminal_setup_open_order", self._event_types())
        events = self.repository.list_events(limit=20)
        event = next(
            e for e in events if e["event_type"] == "reconciliation_terminal_setup_open_order"
        )
        self.assertEqual(event["level"], "WARNING")

    def test_terminal_status_with_open_buy_order_goes_to_manual_review(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.CLOSED.value, "test setup"
        )

        self.reconciliation._update_setup_after_reconciled_order(
            _order(setup_id=self.setup_id, symbol=self.symbol, side="BUY"),
            OrderStatus.SUBMITTED.value,
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("reconciliation_terminal_setup_open_order", self._event_types())


if __name__ == "__main__":
    unittest.main()
