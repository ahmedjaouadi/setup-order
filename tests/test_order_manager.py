from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest, BrokerOrderResult
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.order_manager import (
    DuplicateOrderError,
    ManagementOnlyEntryError,
    OrderManager,
)
from app.models import RiskDecision
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class RejectingBrokerConnector(SimulatedBrokerConnector):
    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        return BrokerOrderResult(
            accepted=False,
            status="REJECTED",
            reason="Rejected by test broker",
        )


class OrderManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()
        self.manager = OrderManager(
            repository=self.repository,
            event_store=self.event_store,
            broker=self.broker,
        )
        setup = BreakoutRetestSetup(valid_breakout_config())
        self.repository.upsert_setup(setup.to_record())

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_places_entry_once_and_then_protects_fill(self) -> None:
        setup = self.repository.get_setup("UEC_2026_001")
        self.assertIsNotNone(setup)
        decision = RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=10,
            entry_price=14.44,
            stop_loss=13.85,
            position_amount_usd=144.40,
            risk_amount_usd=5.90,
        )

        order = await self.manager.place_entry_order(setup, decision)

        self.assertEqual(order.status, "SUBMITTED")
        self.assertEqual(order.trigger_price, 14.44)
        self.assertEqual(order.limit_price, 14.49)
        with self.assertRaises(DuplicateOrderError):
            await self.manager.place_entry_order(setup, decision)

        position = await self.manager.simulate_fill_order(order.id, 14.44)

        self.assertIsNotNone(position)
        self.assertEqual(position.current_stop, 13.85)
        self.assertEqual(len(self.repository.list_orders()), 2)
        self.assertEqual(self.repository.get_setup("UEC_2026_001")["status"], "IN_POSITION")

    async def test_management_only_setup_cannot_place_entry(self) -> None:
        setup = self.repository.get_setup("UEC_2026_001")
        self.assertIsNotNone(setup)
        setup["config"]["setup_role"] = "MANAGEMENT_ONLY"
        decision = RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=10,
            entry_price=14.44,
            stop_loss=13.85,
            position_amount_usd=144.40,
            risk_amount_usd=5.90,
        )

        with self.assertRaises(ManagementOnlyEntryError):
            await self.manager.place_entry_order(setup, decision)

    async def test_rejected_entry_order_requires_manual_review(self) -> None:
        rejecting_manager = OrderManager(
            repository=self.repository,
            event_store=self.event_store,
            broker=RejectingBrokerConnector(),
        )
        setup = self.repository.get_setup("UEC_2026_001")
        self.assertIsNotNone(setup)
        decision = RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=10,
            entry_price=14.44,
            stop_loss=13.85,
            position_amount_usd=144.40,
            risk_amount_usd=5.90,
        )

        order = await rejecting_manager.place_entry_order(setup, decision)

        self.assertEqual(order.status, "REJECTED")
        self.assertEqual(len(self.repository.list_orders()), 1)
        updated = self.repository.get_setup("UEC_2026_001")
        self.assertEqual(updated["status"], "ERROR_REQUIRES_MANUAL_REVIEW")
        self.assertEqual(updated["last_event"], "Rejected by test broker")
        events = self.repository.list_events(limit=5)
        self.assertEqual(events[0]["event_type"], "entry_order_rejected")

        self.repository.delete_order(order.id)

        self.assertIsNone(self.repository.get_order(order.id))
        self.assertEqual(len(self.repository.list_orders()), 0)


if __name__ == "__main__":
    unittest.main()
