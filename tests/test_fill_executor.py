from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest, BrokerOrderResult
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.fill_executor import FillExecutor
from app.engine.order_manager import OrderManager
from app.models import OrderSide, OrderStatus, RiskDecision
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class StopRejectingBrokerConnector(SimulatedBrokerConnector):
    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if request.side == OrderSide.SELL.value:
            return BrokerOrderResult(
                accepted=False,
                status=OrderStatus.REJECTED.value,
                reason="Stop rejected by test broker",
            )
        return await super().submit_order(request)


class FillExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.config = valid_breakout_config()
        self.brokers: list[SimulatedBrokerConnector] = []

    async def asyncTearDown(self) -> None:
        for broker in self.brokers:
            await broker.disconnect()
        self.database.close()
        self.tmp.cleanup()

    async def _executor_with_broker(
        self,
        broker: SimulatedBrokerConnector,
    ) -> tuple[OrderManager, FillExecutor]:
        await broker.connect()
        self.brokers.append(broker)
        setup = BreakoutRetestSetup(self.config)
        self.repository.upsert_setup(setup.to_record())
        manager = OrderManager(
            repository=self.repository,
            event_store=self.event_store,
            broker=broker,
        )
        executor = FillExecutor(
            repository=self.repository,
            event_store=self.event_store,
            broker_provider=lambda: broker,
            stop_order_placer=manager,
        )
        return manager, executor

    async def _place_entry(self, manager: OrderManager):
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        return await manager.place_entry_order(
            setup,
            RiskDecision(
                approved=True,
                reason="Risk approved",
                quantity=10,
                entry_price=14.44,
                stop_loss=13.85,
                position_amount_usd=144.40,
                risk_amount_usd=5.90,
            ),
        )

    async def test_simulated_fill_creates_position_and_protective_stop(self) -> None:
        manager, executor = await self._executor_with_broker(SimulatedBrokerConnector())
        order = await self._place_entry(manager)

        position = await executor.simulate_fill_order(order.id, 14.44)

        self.assertIsNotNone(position)
        self.assertEqual(position.current_stop, 13.85)
        self.assertEqual(
            self.repository.get_setup(self.config["setup_id"])["status"], "IN_POSITION"
        )
        orders = self.repository.list_orders()
        self.assertEqual(len(orders), 2)
        self.assertEqual(self.repository.get_order(order.id)["status"], OrderStatus.FILLED.value)
        event_types = {event["event_type"] for event in self.repository.list_events(limit=5)}
        self.assertIn("entry_filled", event_types)
        self.assertIn("protective_stop_submitted", event_types)

    async def test_stop_rejection_keeps_setup_in_manual_review(self) -> None:
        manager, executor = await self._executor_with_broker(StopRejectingBrokerConnector())
        order = await self._place_entry(manager)

        position = await executor.simulate_fill_order(order.id, 14.44)

        self.assertIsNone(position)
        updated = self.repository.get_setup(self.config["setup_id"])
        self.assertEqual(updated["status"], "ERROR_REQUIRES_MANUAL_REVIEW")
        self.assertEqual(updated["last_event"], "Protective stop submission failed")
        events = {event["event_type"] for event in self.repository.list_events(limit=5)}
        self.assertIn("protective_stop_rejected", events)
        self.assertIn("entry_order_unprotected_blocked", events)
        self.assertEqual(self.repository.get_order(order.id)["status"], OrderStatus.CANCELLED.value)

    async def test_missing_or_non_submitted_order_is_ignored(self) -> None:
        _, executor = await self._executor_with_broker(SimulatedBrokerConnector())

        position = await executor.simulate_fill_order("missing-order", 14.44)

        self.assertIsNone(position)
        self.assertEqual(self.repository.list_positions(), [])


if __name__ == "__main__":
    unittest.main()
