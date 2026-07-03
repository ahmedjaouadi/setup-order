from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.reconciliation import ReconciliationEngine
from app.models import MarketSnapshot, OrderRecord, OrderStatus, SetupRole, SetupStatus, SignalAction
from app.setups.position_management import PositionManagementSetup
from app.setups.setup_roles import (
    entry_policy_errors,
    setup_allows_entry,
    setup_is_management_only,
    setup_role_from_config,
)
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


def valid_management_config() -> dict:
    return {
        "setup_id": "TEST_POSITION_MANAGEMENT_001",
        "symbol": "TEST",
        "setup_type": "position_management",
        "setup_role": "MANAGEMENT_ONLY",
        "direction": "long",
        "enabled": True,
        "mode": "paper",
        "position_source": {
            "mode": "adopt_existing_ibkr_position",
            "require_existing_position": True,
            "reconcile_on_load": True,
            "block_if_position_not_found": True,
        },
        "entry": {"enabled": False},
        "risk": {
            "max_position_amount_usd": 250,
            "max_risk_usd": 15,
            "emergency_exit_if_stop_fails": True,
        },
        "trailing_stop_loss": {
            "enabled": True,
            "mode": "AUTO_INTELLIGENT",
            "never_lower_stop": True,
            "initial_stop": 9.50,
            "broker_order": {
                "order_type": "TRAIL_OR_MANAGED_STOP",
                "attach_to_entry_order": True,
                "required_before_entry_transmission": True,
                "use_native_ibkr_trailing_order_if_available": True,
                "fallback_to_managed_stop_updates": True,
            },
        },
        "management": {
            "never_lower_stop": True,
            "take_profit_mode": "none",
            "stop_management": {"mode": "rule_based", "rules": []},
        },
        "safety": {
            "place_or_update_real_ibkr_stop": True,
            "pause_if_stop_is_missing": True,
            "manual_review_if_market_price_below_stop": True,
        },
    }


class SetupRoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_setup_role_helpers_classify_entry_and_management_roles(self) -> None:
        self.assertEqual(
            setup_role_from_config({"setup_role": "ENTRY_ONLY"}),
            SetupRole.ENTRY_ONLY,
        )
        self.assertTrue(setup_allows_entry("ENTRY_AND_MANAGEMENT"))
        self.assertTrue(setup_allows_entry(SetupRole.ENTRY_ONLY))
        self.assertFalse(setup_allows_entry("MANAGEMENT_ONLY"))
        self.assertTrue(setup_is_management_only("MANAGEMENT_ONLY"))
        self.assertEqual(
            entry_policy_errors("MANAGEMENT_ONLY", True),
            ["MANAGEMENT_ONLY setup cannot enable entry orders"],
        )
        self.assertEqual(
            entry_policy_errors("ENTRY_ONLY", False),
            ["entry.enabled must be true when setup_role allows entries"],
        )

    def test_setup_role_helpers_infer_position_management_role(self) -> None:
        config = {"setup_type": "position_management"}

        self.assertEqual(
            setup_role_from_config(config, infer_position_management=True),
            SetupRole.MANAGEMENT_ONLY,
        )
        self.assertEqual(setup_role_from_config(config), SetupRole.ENTRY_AND_MANAGEMENT)

    def test_management_only_rejects_enabled_entry(self) -> None:
        config = valid_management_config()
        config["entry"]["enabled"] = True

        result = PositionManagementSetup(config).validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("MANAGEMENT_ONLY" in error for error in result.errors))

    def test_management_only_starts_in_reconciliation(self) -> None:
        setup = PositionManagementSetup(valid_management_config())

        self.assertEqual(
            setup.initial_status(),
            SetupStatus.RECONCILING_EXISTING_POSITION,
        )

    def test_structure_based_trailing_raises_stop_after_higher_low(self) -> None:
        config = valid_management_config()
        config["management"]["stop_management"] = {
            "mode": "STRUCTURE_BASED_TRAILING",
            "raise_stop_only_if": {
                "new_higher_low_confirmed": True,
                "confirmation_bars": 2,
                "timeframe": "15m",
            },
        }
        setup = PositionManagementSetup(config)

        signal = setup.evaluate(
            MarketSnapshot(
                symbol="TEST",
                price=10.80,
                bid=10.79,
                ask=10.81,
                last_confirmed_higher_low=10.40,
                minimum_tick=0.01,
                atr_1h=0.50,
                new_higher_low_confirmed=True,
            ),
            SetupStatus.IN_POSITION,
        )

        self.assertEqual(signal.action, SignalAction.RAISE_STOP)
        self.assertEqual(signal.target_status, SetupStatus.MANAGING_POSITION)
        self.assertEqual(signal.new_stop, 10.30)
        self.assertEqual(signal.metadata["rule_id"], "structure_based_trailing")

    def test_structure_based_trailing_waits_for_confirmed_higher_low(self) -> None:
        config = valid_management_config()
        config["management"]["stop_management"] = {
            "mode": "STRUCTURE_BASED_TRAILING",
            "raise_stop_only_if": {"new_higher_low_confirmed": True},
        }
        setup = PositionManagementSetup(config)

        signal = setup.evaluate(
            MarketSnapshot(
                symbol="TEST",
                price=10.80,
                bid=10.79,
                ask=10.81,
                last_confirmed_higher_low=10.40,
                minimum_tick=0.01,
                atr_1h=0.50,
                new_higher_low_confirmed=False,
            ),
            SetupStatus.IN_POSITION,
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(signal.reason, "No management rule reached")

    async def test_reconciliation_adopts_existing_position(self) -> None:
        setup = PositionManagementSetup(valid_management_config())
        self.repository.upsert_setup(setup.to_record())
        entry_order = await self.broker.submit_order(
            BrokerOrderRequest(
                client_order_id="seed-entry",
                setup_id="manual",
                symbol="TEST",
                side="BUY",
                order_type="MKT",
                quantity=10,
            )
        )
        self.assertIsNotNone(entry_order.broker_order_id)
        await self.broker.simulate_fill(entry_order.broker_order_id, 10.00)
        await self.broker.submit_order(
            BrokerOrderRequest(
                client_order_id="seed-stop",
                setup_id="manual",
                symbol="TEST",
                side="SELL",
                order_type="STP",
                quantity=10,
                stop_price=9.50,
            )
        )
        reconciliation = ReconciliationEngine(
            self.repository,
            self.event_store,
            self.broker,
        )

        result = await reconciliation.run()

        self.assertEqual(result["adopted_positions"], 1)
        self.assertEqual(
            self.repository.get_setup("TEST_POSITION_MANAGEMENT_001")["status"],
            SetupStatus.IN_POSITION.value,
        )
        position = self.repository.get_position("TEST")
        self.assertIsNotNone(position)
        self.assertEqual(position["current_stop"], 9.50)

    async def test_reconciliation_marks_missing_broker_order_cancelled(self) -> None:
        setup = PositionManagementSetup(valid_management_config())
        self.repository.upsert_setup(setup.to_record())
        self.repository.update_setup_status(
            setup.config["setup_id"],
            SetupStatus.ENTRY_ORDER_PLACED.value,
            "Order submitted",
        )
        self.repository.upsert_order(
            OrderRecord(
                id="ord_missing",
                setup_id=setup.config["setup_id"],
                symbol="TEST",
                side="BUY",
                order_type="STP_LMT",
                quantity=10,
                status=OrderStatus.SUBMITTED.value,
                broker_order_id="9001",
            )
        )
        reconciliation = ReconciliationEngine(
            self.repository,
            self.event_store,
            self.broker,
        )

        result = await reconciliation.run()

        order = self.repository.get_order("ord_missing")
        self.assertIsNotNone(order)
        self.assertEqual(order["status"], OrderStatus.CANCELLED.value)
        self.assertEqual(result["local_orders_cancelled"], 1)
        self.assertEqual(
            self.repository.get_setup(setup.config["setup_id"])["status"],
            SetupStatus.CANCELLED.value,
        )

    async def test_reconciliation_uses_known_broker_order_status(self) -> None:
        class FilledStatusBroker(SimulatedBrokerConnector):
            async def order_statuses(self) -> dict[str, str]:
                return {"9002": OrderStatus.FILLED.value}

        broker = FilledStatusBroker()
        await broker.connect()
        setup = PositionManagementSetup(valid_management_config())
        self.repository.upsert_setup(setup.to_record())
        self.repository.upsert_order(
            OrderRecord(
                id="ord_filled",
                setup_id=setup.config["setup_id"],
                symbol="TEST",
                side="BUY",
                order_type="MKT",
                quantity=10,
                status=OrderStatus.SUBMITTED.value,
                broker_order_id="9002",
            )
        )
        reconciliation = ReconciliationEngine(
            self.repository,
            self.event_store,
            broker,
        )

        result = await reconciliation.run()

        order = self.repository.get_order("ord_filled")
        self.assertIsNotNone(order)
        self.assertEqual(order["status"], OrderStatus.FILLED.value)
        self.assertEqual(result["local_orders_filled"], 1)

    async def test_reconciliation_reactivates_local_order_still_open_at_broker(self) -> None:
        config = valid_management_config()
        config["position_source"]["block_if_position_not_found"] = False
        setup = PositionManagementSetup(config)
        self.repository.upsert_setup(setup.to_record())
        self.repository.update_setup_status(
            setup.config["setup_id"],
            SetupStatus.CANCELLED.value,
            "Entry order cancelled in TWS",
        )
        broker_result = await self.broker.submit_order(
            BrokerOrderRequest(
                client_order_id="seed-stop",
                setup_id="manual",
                symbol="TEST",
                side="SELL",
                order_type="STP",
                quantity=10,
                stop_price=9.50,
            )
        )
        self.repository.upsert_order(
            OrderRecord(
                id="stp_restored",
                setup_id=setup.config["setup_id"],
                symbol="TEST",
                side="SELL",
                order_type="STP",
                quantity=10,
                status=OrderStatus.CANCELLED.value,
                stop_price=9.50,
                broker_order_id=broker_result.broker_order_id,
            )
        )
        reconciliation = ReconciliationEngine(
            self.repository,
            self.event_store,
            self.broker,
        )

        result = await reconciliation.run()

        order = self.repository.get_order("stp_restored")
        self.assertIsNotNone(order)
        self.assertEqual(order["status"], OrderStatus.SUBMITTED.value)
        self.assertEqual(result["local_orders_reactivated"], 1)
        self.assertEqual(
            self.repository.get_setup(setup.config["setup_id"])["status"],
            SetupStatus.STOP_ORDER_PLACED.value,
        )

    async def test_reconciliation_does_not_cancel_local_orders_when_broker_offline(self) -> None:
        setup = PositionManagementSetup(valid_management_config())
        self.repository.upsert_setup(setup.to_record())
        self.repository.upsert_order(
            OrderRecord(
                id="ord_offline",
                setup_id=setup.config["setup_id"],
                symbol="TEST",
                side="BUY",
                order_type="STP_LMT",
                quantity=10,
                status=OrderStatus.SUBMITTED.value,
                broker_order_id="9003",
            )
        )
        offline_broker = SimulatedBrokerConnector()
        reconciliation = ReconciliationEngine(
            self.repository,
            self.event_store,
            offline_broker,
        )

        result = await reconciliation.run()

        order = self.repository.get_order("ord_offline")
        self.assertIsNotNone(order)
        self.assertEqual(order["status"], OrderStatus.SUBMITTED.value)
        self.assertEqual(result["local_orders_updated"], 0)


if __name__ == "__main__":
    unittest.main()
