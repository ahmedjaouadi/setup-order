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
from app.models import OrderRecord, RiskDecision
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


class StopRejectingBrokerConnector(SimulatedBrokerConnector):
    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if request.side == "SELL":
            return BrokerOrderResult(
                accepted=False,
                status="REJECTED",
                reason="Stop rejected by test broker",
            )
        return await super().submit_order(request)


class StopExplodingBrokerConnector(SimulatedBrokerConnector):
    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if request.side == "SELL":
            raise RuntimeError("Stop placement crashed")
        return await super().submit_order(request)


class MissingCancelBrokerConnector(SimulatedBrokerConnector):
    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        return BrokerOrderResult(
            accepted=False,
            status="REJECTED",
            broker_order_id=broker_order_id,
            reason="Order not found in TWS session",
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

    async def test_places_entry_as_bracket_and_then_keeps_stop_after_fill(self) -> None:
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
        orders = self.repository.list_orders()
        self.assertEqual(len(orders), 2)
        stop_order = next(item for item in orders if item["side"] == "SELL")
        self.assertEqual(stop_order["parent_id"], order.id)
        self.assertEqual(stop_order["stop_price"], 13.85)
        broker_requests = list(self.broker._orders.values())
        broker_entry = next(item for item in broker_requests if item.side == "BUY")
        broker_stop = next(item for item in broker_requests if item.side == "SELL")
        self.assertFalse(broker_entry.transmit)
        self.assertTrue(broker_stop.transmit)
        self.assertEqual(broker_stop.parent_id, order.broker_order_id)
        self.assertEqual(broker_stop.stop_price, 13.85)
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

    async def test_entry_order_transmission_requires_trailing_stop_loss_enabled(self) -> None:
        setup = self.repository.get_setup("UEC_2026_001")
        self.assertIsNotNone(setup)
        setup["config"]["trailing_stop_loss"]["enabled"] = False
        decision = RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=10,
            entry_price=14.44,
            stop_loss=13.85,
            position_amount_usd=144.40,
            risk_amount_usd=5.90,
        )

        with self.assertRaisesRegex(ValueError, "Trailing stop-loss initial stop"):
            await self.manager.place_entry_order(setup, decision)

        self.assertEqual(self.repository.list_orders(), [])

    async def test_entry_order_transmission_requires_trailing_stop_broker_ready(self) -> None:
        setup = self.repository.get_setup("UEC_2026_001")
        self.assertIsNotNone(setup)
        setup["config"]["trailing_stop_loss"]["broker_order"]["trailing_stop_order_ready"] = False
        decision = RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=10,
            entry_price=14.44,
            stop_loss=13.85,
            position_amount_usd=144.40,
            risk_amount_usd=5.90,
        )

        with self.assertRaisesRegex(ValueError, "BLOCKED_TRAILING_STOP_NOT_READY"):
            await self.manager.place_entry_order(setup, decision)

        self.assertEqual(self.repository.list_orders(), [])

    async def test_attach_missing_stop_uses_setup_stop_and_broker_parent(self) -> None:
        setup = self.repository.get_setup("UEC_2026_001")
        self.assertIsNotNone(setup)
        entry_order = OrderRecord(
            id="ord_unprotected",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            side="BUY",
            order_type="STP_LMT",
            quantity=10,
            status="SUBMITTED",
            trigger_price=14.44,
            limit_price=14.49,
            broker_order_id="9001",
        )
        self.repository.upsert_order(entry_order)

        stop_order = await self.manager.attach_missing_stop(entry_order.id)

        self.assertEqual(stop_order.side, "SELL")
        self.assertEqual(stop_order.parent_id, entry_order.id)
        self.assertEqual(stop_order.stop_price, 13.85)
        persisted = self.repository.get_order(stop_order.id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["parent_id"], entry_order.id)
        broker_stop = next(item for item in self.broker._orders.values() if item.side == "SELL")
        self.assertEqual(broker_stop.parent_id, "9001")
        self.assertTrue(broker_stop.transmit)

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

    async def test_rejected_stop_cancels_parent_and_flags_manual_review(self) -> None:
        broker = StopRejectingBrokerConnector()
        await broker.connect()
        manager = OrderManager(
            repository=self.repository,
            event_store=self.event_store,
            broker=broker,
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

        order = await manager.place_entry_order(setup, decision)

        updated = self.repository.get_setup("UEC_2026_001")
        self.assertEqual(updated["status"], "ERROR_REQUIRES_MANUAL_REVIEW")
        self.assertEqual(updated["last_event"], "Protective stop submission failed")
        parent = self.repository.get_order(order.id)
        self.assertIsNotNone(parent)
        self.assertEqual(parent["status"], "CANCELLED")
        events = {event["event_type"] for event in self.repository.list_events(limit=5)}
        self.assertIn("protective_stop_rejected", events)
        self.assertIn("entry_order_unprotected_blocked", events)

    async def test_unexpected_stop_error_cancels_parent_and_flags_manual_review(self) -> None:
        broker = StopExplodingBrokerConnector()
        await broker.connect()
        manager = OrderManager(
            repository=self.repository,
            event_store=self.event_store,
            broker=broker,
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

        order = await manager.place_entry_order(setup, decision)

        parent = self.repository.get_order(order.id)
        self.assertIsNotNone(parent)
        self.assertEqual(parent["status"], "CANCELLED")
        updated = self.repository.get_setup("UEC_2026_001")
        self.assertEqual(updated["status"], "ERROR_REQUIRES_MANUAL_REVIEW")
        self.assertIn("Protective stop submission raised an exception", updated["last_event"])
        events = self.repository.list_events(limit=5)
        self.assertEqual(events[0]["event_type"], "entry_order_unprotected_blocked")

    async def test_cancel_marks_order_cancelled_when_broker_already_missing_it(self) -> None:
        broker = MissingCancelBrokerConnector()
        await broker.connect()
        manager = OrderManager(
            repository=self.repository,
            event_store=self.event_store,
            broker=broker,
        )
        setup = self.repository.get_setup("UEC_2026_001")
        self.assertIsNotNone(setup)
        self.repository.upsert_order(
            OrderRecord(
                id="ord_already_cancelled",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                side="BUY",
                order_type="STP_LMT",
                quantity=10,
                status="SUBMITTED",
                broker_order_id="9001",
            )
        )

        cancelled = await manager.cancel_order("ord_already_cancelled")

        self.assertTrue(cancelled)
        order = self.repository.get_order("ord_already_cancelled")
        self.assertIsNotNone(order)
        self.assertEqual(order["status"], "CANCELLED")
        events = self.repository.list_events(limit=3)
        self.assertEqual(events[0]["event_type"], "order_cancel_reconciled")


if __name__ == "__main__":
    unittest.main()
