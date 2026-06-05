from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.reconciliation import ReconciliationEngine
from app.models import MarketSnapshot, SetupStatus, SignalAction
from app.setups.position_management import PositionManagementSetup
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
        "mode": "simulation",
        "position_source": {
            "mode": "adopt_existing_ibkr_position",
            "require_existing_position": True,
            "reconcile_on_load": True,
            "block_if_position_not_found": True,
        },
        "entry": {"enabled": False},
        "risk": {
            "protective_stop": 9.50,
            "emergency_exit_if_stop_fails": True,
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


if __name__ == "__main__":
    unittest.main()
