from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.position_manager import PositionManager
from app.engine.stop_modification_service import (
    REASON_BROKER_REJECTED,
    REASON_NO_STOP_TARGET,
    REASON_STOP_LOWERING_FORBIDDEN,
    StopModificationService,
)
from app.engine.trade_guards import (
    REASON_HALT_ACTIVE,
    REASON_OUTSIDE_TRADING_WINDOW,
    TradeGuardsService,
)
from app.models import OrderRecord, PositionRecord
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class StopModificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()
        self.position_manager = PositionManager(self.repository, self.event_store)
        self.service = StopModificationService(
            self.repository,
            self.event_store,
            self.broker,
            self.position_manager,
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def _seed_position(self, current_stop: float | None = 18.0) -> None:
        self.repository.upsert_position(
            PositionRecord(
                symbol="LUNR",
                setup_id="LUNR_SETUP",
                quantity=6,
                average_price=20.0,
                current_price=21.0,
                unrealized_pnl=6.0,
                current_stop=current_stop,
                risk_remaining=12.0,
                status="OPEN",
            )
        )

    async def _seed_stop_order_at_broker(self, stop_price: float = 18.0) -> str:
        request = BrokerOrderRequest(
            client_order_id="stp_LUNR_1",
            setup_id="LUNR_SETUP",
            symbol="LUNR",
            side="SELL",
            order_type="STP",
            quantity=6,
            stop_price=stop_price,
        )
        result = await self.broker.submit_order(request)
        assert result.broker_order_id is not None
        self.repository.upsert_order(
            OrderRecord(
                id="stp_LUNR_1",
                setup_id="LUNR_SETUP",
                symbol="LUNR",
                side="SELL",
                order_type="STP",
                quantity=6,
                status="SUBMITTED",
                stop_price=stop_price,
                broker_order_id=result.broker_order_id,
            )
        )
        return result.broker_order_id

    async def test_raise_stop_updates_broker_order_local_order_and_position(self) -> None:
        await self._seed_position(current_stop=18.0)
        broker_order_id = await self._seed_stop_order_at_broker(18.0)

        result = await self.service.modify_stop("LUNR", 19.5)

        self.assertTrue(result["ok"])
        self.assertTrue(result["broker_updated"])
        broker_order = (await self.broker.open_orders())[0]
        self.assertEqual(broker_order.stop_price, 19.5)
        self.assertEqual(broker_order.broker_order_id, broker_order_id)
        local_order = self.repository.get_order("stp_LUNR_1")
        assert local_order is not None
        self.assertEqual(local_order["stop_price"], 19.5)
        position = self.repository.get_position("LUNR")
        assert position is not None
        self.assertEqual(position["current_stop"], 19.5)

    async def test_lowering_stop_is_rejected_before_touching_broker(self) -> None:
        await self._seed_position(current_stop=18.0)
        await self._seed_stop_order_at_broker(18.0)

        result = await self.service.modify_stop("LUNR", 17.0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], REASON_STOP_LOWERING_FORBIDDEN)
        broker_order = (await self.broker.open_orders())[0]
        self.assertEqual(broker_order.stop_price, 18.0)
        position = self.repository.get_position("LUNR")
        assert position is not None
        self.assertEqual(position["current_stop"], 18.0)

    async def test_broker_rejection_leaves_local_state_untouched(self) -> None:
        await self._seed_position(current_stop=18.0)
        await self._seed_stop_order_at_broker(18.0)
        # Point the local order at a broker id TWS does not know: the broker
        # rejects, and no local record may change.
        self.repository.update_order_status("stp_LUNR_1", "SUBMITTED")
        order = self.repository.get_order("stp_LUNR_1")
        assert order is not None
        self.repository.upsert_order(
            OrderRecord(
                id="stp_LUNR_1",
                setup_id="LUNR_SETUP",
                symbol="LUNR",
                side="SELL",
                order_type="STP",
                quantity=6,
                status="SUBMITTED",
                stop_price=18.0,
                broker_order_id="unknown-broker-id",
            )
        )

        result = await self.service.modify_stop("LUNR", 19.5)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], REASON_BROKER_REJECTED)
        local_order = self.repository.get_order("stp_LUNR_1")
        assert local_order is not None
        self.assertEqual(local_order["stop_price"], 18.0)
        position = self.repository.get_position("LUNR")
        assert position is not None
        self.assertEqual(position["current_stop"], 18.0)

    async def test_position_without_stop_order_updates_locally(self) -> None:
        await self._seed_position(current_stop=18.0)

        result = await self.service.modify_stop("LUNR", 19.0)

        self.assertTrue(result["ok"])
        self.assertFalse(result["broker_updated"])
        position = self.repository.get_position("LUNR")
        assert position is not None
        self.assertEqual(position["current_stop"], 19.0)

    async def test_unknown_symbol_is_rejected(self) -> None:
        result = await self.service.modify_stop("GHOST", 19.0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], REASON_NO_STOP_TARGET)

    async def test_disconnected_broker_falls_back_to_local_update(self) -> None:
        await self._seed_position(current_stop=18.0)
        await self._seed_stop_order_at_broker(18.0)
        await self.broker.disconnect()

        result = await self.service.modify_stop("LUNR", 19.5)

        self.assertTrue(result["ok"])
        self.assertFalse(result["broker_updated"])
        position = self.repository.get_position("LUNR")
        assert position is not None
        self.assertEqual(position["current_stop"], 19.5)


class StopModificationGuardTests(unittest.IsolatedAsyncioTestCase):
    """Invariant 4: stop modifications go through trade_guards too."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.guards = TradeGuardsService(
            self.repository,
            {"trade_guards": {"enabled": True}},
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_market_closed_weekend_blocks_stop_modification(self) -> None:
        saturday = datetime(2026, 7, 4, 15, 0, tzinfo=UTC)

        verdict = self.guards.evaluate_stop_modification("LUNR", now=saturday)

        assert verdict is not None
        self.assertEqual(verdict.reason_code, REASON_OUTSIDE_TRADING_WINDOW)
        self.assertEqual(verdict.decision_status, "MARKET_CLOSED")

    def test_open_session_allows_stop_modification(self) -> None:
        tuesday_rth = datetime(2026, 7, 7, 15, 0, tzinfo=UTC)  # 11:00 New York

        self.assertIsNone(self.guards.evaluate_stop_modification("LUNR", now=tuesday_rth))

    def test_halted_symbol_blocks_stop_modification(self) -> None:
        self.guards.set_halt_state("LUNR", halted=True)
        tuesday_rth = datetime(2026, 7, 7, 15, 0, tzinfo=UTC)

        verdict = self.guards.evaluate_stop_modification("LUNR", now=tuesday_rth)

        assert verdict is not None
        self.assertEqual(verdict.reason_code, REASON_HALT_ACTIVE)

    async def test_service_rejects_when_guard_blocks_and_broker_is_untouched(self) -> None:
        self.guards.set_halt_state("LUNR", halted=True)
        broker = SimulatedBrokerConnector()
        await broker.connect()
        submit = await broker.submit_order(
            BrokerOrderRequest(
                client_order_id="stp_LUNR_1",
                setup_id="LUNR_SETUP",
                symbol="LUNR",
                side="SELL",
                order_type="STP",
                quantity=6,
                stop_price=18.0,
            )
        )
        assert submit.broker_order_id is not None
        self.repository.upsert_order(
            OrderRecord(
                id="stp_LUNR_1",
                setup_id="LUNR_SETUP",
                symbol="LUNR",
                side="SELL",
                order_type="STP",
                quantity=6,
                status="SUBMITTED",
                stop_price=18.0,
                broker_order_id=submit.broker_order_id,
            )
        )
        service = StopModificationService(
            self.repository,
            self.event_store,
            broker,
            PositionManager(self.repository, self.event_store),
            trade_guards=self.guards,
        )

        result = await service.modify_stop("LUNR", 19.5)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], REASON_HALT_ACTIVE)
        broker_order = (await broker.open_orders())[0]
        self.assertEqual(broker_order.stop_price, 18.0)
        local_order = self.repository.get_order("stp_LUNR_1")
        assert local_order is not None
        self.assertEqual(local_order["stop_price"], 18.0)


class BrokerOnlyOrderCancelTests(unittest.IsolatedAsyncioTestCase):
    """Rows built from unmatched TWS orders (id broker_<id>) must be cancellable."""

    async def test_cancel_broker_only_order_by_synthetic_id(self) -> None:
        from app.engine.order_manager import OrderManager

        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = TradingRepository(database)
            event_store = EventStore(repository)
            broker = SimulatedBrokerConnector()
            await broker.connect()
            submit = await broker.submit_order(
                BrokerOrderRequest(
                    client_order_id="external_1",
                    setup_id="broker",
                    symbol="LUNR",
                    side="BUY",
                    order_type="MKT",
                    quantity=5,
                )
            )
            assert submit.broker_order_id is not None
            manager = OrderManager(repository, event_store, broker)

            cancelled = await manager.cancel_order(f"broker_{submit.broker_order_id}")

            self.assertTrue(cancelled)
            self.assertEqual(await broker.open_orders(), [])
            database.close()

    async def test_cancel_unknown_local_id_still_returns_false(self) -> None:
        from app.engine.order_manager import OrderManager

        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = TradingRepository(database)
            event_store = EventStore(repository)
            broker = SimulatedBrokerConnector()
            await broker.connect()
            manager = OrderManager(repository, event_store, broker)

            self.assertFalse(await manager.cancel_order("ord_unknown"))
            database.close()


class SimulatedBrokerModifyStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_modify_unknown_order_is_rejected(self) -> None:
        broker = SimulatedBrokerConnector()
        await broker.connect()

        result = await broker.modify_stop_order("404", 19.0)

        self.assertFalse(result.accepted)

    async def test_modify_stop_limit_order_moves_trigger_too(self) -> None:
        broker = SimulatedBrokerConnector()
        await broker.connect()
        submit = await broker.submit_order(
            BrokerOrderRequest(
                client_order_id="stp_1",
                setup_id="S",
                symbol="LUNR",
                side="SELL",
                order_type="STP_LMT",
                quantity=6,
                trigger_price=18.0,
                limit_price=17.9,
                stop_price=18.0,
            )
        )
        assert submit.broker_order_id is not None

        result = await broker.modify_stop_order(submit.broker_order_id, 19.0)

        self.assertTrue(result.accepted)
        order = (await broker.open_orders())[0]
        self.assertEqual(order.stop_price, 19.0)
        self.assertEqual(order.trigger_price, 19.0)


if __name__ == "__main__":
    unittest.main()
