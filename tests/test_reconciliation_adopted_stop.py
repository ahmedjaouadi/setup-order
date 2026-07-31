from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest, BrokerPosition
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.reconciliation import ReconciliationEngine
from app.models import OrderRecord, OrderStatus, OrderType, SetupRecord, SetupStatus
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

"""B-1a (audits 76, 80): the adoption loop for MANAGEMENT_ONLY setups in
adopt_existing_ibkr_position mode sees the broker's protective stop
(_matching_stop_order, reconciliation.py) but, before this lot, never wrote
it to the local `orders` table. StopModificationService (root B, B-1b) can
only find a broker_order_id to modify via active_stop_order_for_symbol,
which reads that table -- so without this lot, B-1b would silently no-op
for every adopted setup. These tests cover the four proof points from
audit/ORDRE_B1a.md section 5: reachability by the B-1b lookup, idempotence
across repeated reconciliation cycles, anti-theft when another setup
already owns the symbol's active stop row, and the dormant A-3 false
positive at a second startup."""


def _adoption_config(setup_id: str, symbol: str, *, initial_stop: float = 90.0) -> dict:
    return {
        "setup_id": setup_id,
        "symbol": symbol,
        "enabled": True,
        "mode": "paper",
        "setup_type": "position_management",
        "setup_role": "MANAGEMENT_ONLY",
        "direction": "long",
        "entry": {"enabled": False},
        "trailing_stop_loss": {"initial_stop": initial_stop, "current_stop": initial_stop},
        "position_source": {
            "mode": "adopt_existing_ibkr_position",
            "block_if_position_not_found": True,
        },
        "safety": {"pause_if_stop_is_missing": False},
    }


class _AdoptionBrokerWithStop(SimulatedBrokerConnector):
    """Reports exactly one broker position and one active protective SELL
    stop for the same symbol -- the scenario the adoption loop's
    _matching_stop_order (reconciliation.py:236) is built to see."""

    def __init__(
        self, position: BrokerPosition, *, stop_price: float, broker_order_id: str
    ) -> None:
        super().__init__()
        self._position = position
        self._stop_price = stop_price
        self._stop_broker_order_id = broker_order_id

    async def positions(self) -> list[BrokerPosition]:
        return [self._position]

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return [
            BrokerOrderRequest(
                client_order_id="broker-stop-1",
                setup_id="broker",
                symbol=self._position.symbol,
                side="SELL",
                order_type=OrderType.STP.value,
                quantity=int(self._position.quantity),
                stop_price=self._stop_price,
                status=OrderStatus.SUBMITTED.value,
                broker_order_id=self._stop_broker_order_id,
                broker_perm_id="perm-" + self._stop_broker_order_id,
            )
        ]


class PersistAdoptedStopOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.setup_id = "ADOPT_2026_001"
        self.symbol = "ADPT"
        self.repository.upsert_setup(
            SetupRecord(
                setup_id=self.setup_id,
                symbol=self.symbol,
                setup_type="position_management",
                enabled=True,
                mode="paper",
                status=SetupStatus.RECONCILING_EXISTING_POSITION.value,
                entry_zone="",
                stop_loss=90.0,
                risk_amount=None,
                order_status="",
                position_status="",
                last_event="test setup",
                config=_adoption_config(self.setup_id, self.symbol),
            )
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _make_broker(
        self, *, broker_order_id: str = "9002", stop_price: float = 95.0
    ) -> _AdoptionBrokerWithStop:
        return _AdoptionBrokerWithStop(
            BrokerPosition(
                symbol=self.symbol,
                quantity=100,
                average_price=98.0,
                current_price=100.0,
            ),
            stop_price=stop_price,
            broker_order_id=broker_order_id,
        )

    async def test_adopted_stop_is_persisted_and_reachable_by_stop_modification_lookup(
        self,
    ) -> None:
        broker = self._make_broker(broker_order_id="9002")
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)

        await reconciliation.run()

        orders = self.repository.list_orders()
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order["broker_order_id"], "9002")
        self.assertEqual(order["setup_id"], self.setup_id)
        # This is the exact lookup StopModificationService.modify_stop uses
        # (stop_modification_service.py:67) -- B-1b's entire dependency on
        # B-1a is that this returns non-None with a broker_order_id set.
        found = self.repository.active_stop_order_for_symbol(self.symbol)
        self.assertIsNotNone(found)
        self.assertEqual(found["broker_order_id"], "9002")

    async def test_setup_id_is_adopting_setup_never_the_broker_placeholder(self) -> None:
        broker = self._make_broker(broker_order_id="9002")
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)

        await reconciliation.run()

        order = self.repository.list_orders()[0]
        self.assertEqual(order["setup_id"], self.setup_id)
        self.assertNotEqual(order["setup_id"], "broker")

    async def test_two_consecutive_runs_create_a_single_order_row(self) -> None:
        broker = self._make_broker(broker_order_id="9002")
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)

        await reconciliation.run()
        await reconciliation.run()

        orders = self.repository.list_orders()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["broker_order_id"], "9002")

    async def test_does_not_steal_stop_order_owned_by_another_setup(self) -> None:
        other_setup_id = "OTHER_SETUP_001"
        self.repository.upsert_order(
            OrderRecord(
                id="ord-other-stop-1",
                setup_id=other_setup_id,
                symbol=self.symbol,
                side="SELL",
                order_type=OrderType.STP.value,
                quantity=50,
                status=OrderStatus.SUBMITTED.value,
                stop_price=93.0,
                broker_order_id="7777",
            )
        )
        # The other setup's stop must still be reported as an open broker
        # order this cycle too -- otherwise _reconcile_local_orders (which
        # runs before the adoption loop, reconciliation.py:161) would infer
        # it vanished and mark it CANCELLED, making it invisible to
        # active_stop_order_for_symbol before the anti-theft check ever
        # runs. Listing "9002" first ensures _matching_stop_order (first
        # SELL match by symbol) picks the adopting setup's own stop, not
        # the other setup's.
        class _BrokerWithTwoActiveStops(_AdoptionBrokerWithStop):
            async def open_orders(self) -> list[BrokerOrderRequest]:
                own = await super().open_orders()
                return own + [
                    BrokerOrderRequest(
                        client_order_id="broker-stop-other",
                        setup_id="broker",
                        symbol=self._position.symbol,
                        side="SELL",
                        order_type=OrderType.STP.value,
                        quantity=50,
                        stop_price=93.0,
                        status=OrderStatus.SUBMITTED.value,
                        broker_order_id="7777",
                    )
                ]

        broker = _BrokerWithTwoActiveStops(
            BrokerPosition(
                symbol=self.symbol,
                quantity=100,
                average_price=98.0,
                current_price=100.0,
            ),
            stop_price=95.0,
            broker_order_id="9002",
        )
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)

        await reconciliation.run()

        orders = self.repository.list_orders()
        self.assertEqual(len(orders), 2)
        other_row = next(o for o in orders if o["id"] == "ord-other-stop-1")
        self.assertEqual(other_row["setup_id"], other_setup_id)
        self.assertEqual(other_row["broker_order_id"], "7777")
        new_row = next(o for o in orders if o["id"] != "ord-other-stop-1")
        self.assertEqual(new_row["setup_id"], self.setup_id)
        self.assertEqual(new_row["broker_order_id"], "9002")
        events = {e["event_type"] for e in self.repository.list_events(limit=20)}
        self.assertIn("adoption_stop_order_owner_conflict", events)

    async def test_a3_false_positive_no_longer_fires_on_second_startup(self) -> None:
        """Before B-1a: a MANAGEMENT_ONLY setup adopted in a prior cycle has
        a `positions` row but no `orders` row (the stop was seen but never
        persisted) -- _protection_snapshot's first branch
        (`open_position and active_stop_order is None`) fires
        unconditionally, raising a false CRITICAL
        "startup_filled_entry_without_stop" on the very next
        reconciliation.run(startup=True), even though the position is
        genuinely protected at the broker. After B-1a, the stop is
        persisted the first time the loop sees it, so the second startup's
        _detect_unprotected_entry_orphans (which runs before the adoption
        loop each cycle) finds an active stop and does not alarm."""
        broker = self._make_broker(broker_order_id="9002")
        await broker.connect()
        first_cycle = ReconciliationEngine(self.repository, self.event_store, broker)
        await first_cycle.run()

        # Simulate an application restart: a fresh engine instance reusing
        # the same repository (same on-disk state), reporting the same
        # broker reality this cycle.
        second_broker = self._make_broker(broker_order_id="9002")
        await second_broker.connect()
        second_cycle = ReconciliationEngine(self.repository, self.event_store, second_broker)

        await second_cycle.run(startup=True)

        setup = self.repository.get_setup(self.setup_id)
        self.assertNotEqual(setup["status"], SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        events = {e["event_type"] for e in self.repository.list_events(limit=20)}
        self.assertNotIn("startup_filled_entry_without_stop", events)


if __name__ == "__main__":
    unittest.main()
