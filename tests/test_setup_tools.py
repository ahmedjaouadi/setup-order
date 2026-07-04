from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.api import routes_setups
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.trading_engine import TradingEngine
from app.models import OrderRecord, OrderSide, OrderStatus, PositionRecord, SetupStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.breakout_retest import BreakoutRetestSetup
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config, valid_momentum_config


class SetupToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"]["database_file"] = str(root / "state.sqlite")
        config["storage"]["setups_folder"] = str(root / "setups")
        config["storage"]["logs_folder"] = str(root / "logs")
        self.settings = Settings.from_dict(config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()
        self.engine = TradingEngine(
            settings=self.settings,
            repository=self.repository,
            broker=self.broker,
        )

    async def asyncTearDown(self) -> None:
        await self.broker.disconnect()
        self.database.close()
        self.tmp.cleanup()

    async def test_set_all_setups_enabled_updates_every_setup(self) -> None:
        breakout = BreakoutRetestSetup(valid_breakout_config())
        momentum = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(breakout.to_record())
        self.repository.upsert_setup(momentum.to_record())

        disabled = await self.engine.set_all_setups_enabled(False)

        self.assertEqual(disabled["updated_count"], 2)
        self.assertFalse(self.repository.get_setup(breakout.setup_id)["enabled"])
        self.assertFalse(self.repository.get_setup(momentum.setup_id)["enabled"])

        enabled = await self.engine.set_all_setups_enabled(True)

        self.assertEqual(enabled["updated_count"], 2)
        self.assertTrue(self.repository.get_setup(breakout.setup_id)["enabled"])
        self.assertTrue(self.repository.get_setup(momentum.setup_id)["enabled"])

    async def test_load_all_preserves_existing_enabled_state(self) -> None:
        config = valid_breakout_config()
        setup_id = config["setup_id"]

        self.engine.setup_engine.create_or_update_from_config(config)
        self.repository.set_setup_enabled(setup_id, False)

        self.engine.setup_engine.load_all()

        self.assertFalse(self.repository.get_setup(setup_id)["enabled"])

    async def test_new_saved_setup_starts_disarmed_until_explicit_arm(self) -> None:
        config = valid_breakout_config()
        config["enabled"] = False

        self.engine.setup_engine.create_or_update_from_config(config)

        setup = self.repository.get_setup(config["setup_id"])
        self.assertFalse(setup["enabled"])
        self.assertEqual(setup["status"], SetupStatus.DISABLED.value)

    async def test_save_existing_setup_preserves_armed_runtime_status(self) -> None:
        config = valid_breakout_config()
        setup = BreakoutRetestSetup(config)
        self.repository.upsert_setup(setup.to_record(SetupStatus.WAITING_ACTIVATION))

        result = self.engine.setup_engine.create_or_update_from_config(config)

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(
            self.repository.get_setup(setup.setup_id)["status"],
            SetupStatus.WAITING_ACTIVATION.value,
        )

    async def test_save_existing_setup_preserves_disarmed_runtime_status(self) -> None:
        config = valid_breakout_config()
        setup = BreakoutRetestSetup(config)
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))

        result = self.engine.setup_engine.create_or_update_from_config(config)

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(
            self.repository.get_setup(setup.setup_id)["status"],
            SetupStatus.DISABLED.value,
        )

    async def test_save_setup_accepts_template_wrapper_json(self) -> None:
        config = valid_momentum_config()
        config["setup_id"] = "KLIC_20260614_001"
        config["symbol"] = "KLIC"
        config["breakout"]["resistance"] = 115.15
        config["entry"]["maximum_limit_price"] = 115.20
        config["risk"].pop("initial_stop_loss", None)
        config["trailing_stop_loss"] = {
            "enabled": True,
            "mode": "AUTO_INTELLIGENT",
            "initial_stop": 109.80,
            "never_lower_stop": True,
            "broker_order": {
                "order_type": "TRAIL_OR_MANAGED_STOP",
                "attach_to_entry_order": True,
                "required_before_entry_transmission": True,
            },
        }
        config["rearm"] = {
            "new_local_resistance": None,
            "new_trigger": None,
            "new_limit": None,
        }
        wrapper = {
            "skeleton": {
                **config,
                "setup_type_options": ["momentum_breakout", "breakout_retest"],
                "volume_confirmation_policy_by_setup_type": {
                    "momentum_breakout": {"required_for_entry": True},
                },
                "expected_output": {
                    "format": "FINAL_CANONICAL_SETUP_ONLY",
                    "rules": ["return only one final setup JSON"],
                },
                "_template": {
                    "template_kind": "TEMPLATE_UNIVERSEL_DE_DEMANDE",
                },
            },
            "supported_setup_types": ["momentum_breakout"],
            "required_fields": ["setup_id", "symbol"],
        }

        result = await self.engine.save_setup(wrapper)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["setup"]["setup_id"], "KLIC_20260614_001")
        self.assertEqual(result["setup"]["symbol"], "KLIC")
        self.assertEqual(result["setup"]["status"], SetupStatus.DISABLED.value)
        self.assertNotIn("setup_type_options", result["setup"]["config"])
        self.assertNotIn("volume_confirmation_policy_by_setup_type", result["setup"]["config"])
        self.assertNotIn("expected_output", result["setup"]["config"])
        self.assertTrue(
            any("Setup template wrapper detected" in warning for warning in result["warnings"])
        )

    async def test_arm_setup_requires_trailing_broker_order_requirement(self) -> None:
        config = valid_momentum_config()
        config["trailing_stop_loss"] = {
            "enabled": True,
            "mode": "AUTO_INTELLIGENT",
            "initial_stop": 14.90,
            "never_lower_stop": True,
            "broker_order": {
                "order_type": "TRAIL_OR_MANAGED_STOP",
                "attach_to_entry_order": True,
                "required_before_entry_transmission": False,
            },
        }
        setup = MomentumBreakoutSetup(config)
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))

        result = await self.engine.arm_setup(setup.setup_id)

        self.assertFalse(result["ok"])
        self.assertIn(
            "trailing_stop_loss.broker_order.required_before_entry_transmission must be true before arming",
            result["errors"],
        )

    async def test_arm_setup_requires_trailing_stop_loss_enabled(self) -> None:
        config = valid_momentum_config()
        config["trailing_stop_loss"]["enabled"] = False
        setup = MomentumBreakoutSetup(config)
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))

        result = await self.engine.arm_setup(setup.setup_id)

        self.assertFalse(result["ok"])
        self.assertIn("TRAILING_STOP_LOSS_REQUIRED", result["errors"])

    async def test_arm_setup_changes_runtime_status_without_resaving(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))

        result = await self.engine.arm_setup(setup.setup_id)

        self.assertTrue(result["ok"], result)
        saved = self.repository.get_setup(setup.setup_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], SetupStatus.WAITING_ACTIVATION.value)
        self.assertEqual(result["setup"]["status"], SetupStatus.WAITING_ACTIVATION.value)

    async def test_disarm_setup_changes_runtime_status_without_resaving(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.WAITING_ACTIVATION))

        result = await self.engine.disarm_setup(setup.setup_id)

        self.assertTrue(result["ok"], result)
        saved = self.repository.get_setup(setup.setup_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], SetupStatus.DISABLED.value)
        self.assertEqual(result["setup"]["status"], SetupStatus.DISABLED.value)

    async def test_arm_setup_route_rejects_non_armable_setup(self) -> None:
        config = valid_momentum_config()
        config["enabled"] = False
        setup = MomentumBreakoutSetup(config)
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=self.engine)))

        with self.assertRaises(HTTPException) as ctx:
            await routes_setups.arm_setup(request, setup.setup_id)

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("setup.enabled must be true before arming", ctx.exception.detail["errors"])

    async def test_disarm_setup_route_updates_runtime_status(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.WAITING_ACTIVATION))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=self.engine)))

        result = await routes_setups.disarm_setup(request, setup.setup_id)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["setup"]["status"], SetupStatus.DISABLED.value)

    async def test_disarm_setup_is_blocked_with_active_order(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.WAITING_ACTIVATION))
        self.repository.upsert_order(
            OrderRecord(
                id="ORDER_ACTIVE",
                setup_id=setup.setup_id,
                symbol=setup.symbol,
                side=OrderSide.BUY.value,
                order_type="STP_LMT",
                quantity=10,
                status=OrderStatus.SUBMITTED.value,
            )
        )

        with self.assertRaises(ValueError) as ctx:
            await self.engine.disarm_setup(setup.setup_id)

        self.assertIn("active orders", str(ctx.exception))
        self.assertEqual(
            self.repository.get_setup(setup.setup_id)["status"],
            SetupStatus.WAITING_ACTIVATION.value,
        )
        status = self.engine.setup_arm_status(setup.setup_id)
        self.assertFalse(status["disarmable"])
        self.assertIn("active orders", " ".join(status["disarm_validation"]["errors"]))

    async def test_disarm_setup_route_is_blocked_with_open_position(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.IN_POSITION))
        self.repository.upsert_position(
            PositionRecord(
                setup_id=setup.setup_id,
                symbol=setup.symbol,
                quantity=10,
                average_price=15.85,
                current_price=16.10,
                unrealized_pnl=2.50,
                current_stop=14.90,
                risk_remaining=9.50,
                status=SetupStatus.IN_POSITION.value,
            )
        )
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=self.engine)))

        with self.assertRaises(HTTPException) as ctx:
            await routes_setups.disarm_setup(request, setup.setup_id)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("open position", ctx.exception.detail)

    def test_setup_config_template_describes_required_fields(self) -> None:
        template = self.engine.setup_config_template()

        self.assertIn("skeleton", template)
        self.assertEqual(template["skeleton"]["setup_type"], "CHOOSE_ONE_SETUP_TYPE")
        self.assertIn("setup_type_options", template["skeleton"])
        self.assertEqual(template["skeleton"]["entry"]["order_type"], "AUTO_SELECT")
        self.assertIn("expected_output", template["skeleton"])
        self.assertIn("volume_confirmation_policy_by_setup_type", template["skeleton"])
        self.assertIn("trailing_runner", template["supported_setup_types"])
        self.assertIn(
            "breakout.resistance", template["required_by_setup_type"]["momentum_breakout"]
        )
        self.assertIn(
            "trailing_stop_loss.enabled=true",
            template["required_by_setup_type"]["momentum_breakout"],
        )
        # MANAGEMENT_ONLY types adopt an existing position: they gate on the
        # existing protective stop, not on an entry order to transmit.
        self.assertIn(
            "trailing_stop_loss.current_stop before arming",
            template["required_by_setup_type"]["trailing_runner"],
        )
        self.assertIn(
            "never generate an initial BUY order",
            template["required_by_setup_type"]["trailing_runner"],
        )
        self.assertNotIn(
            "trailing_stop_loss.broker_order.required_before_entry_transmission=true",
            template["required_by_setup_type"]["trailing_runner"],
        )
        self.assertIn("trailing_stop_loss.initial_stop", template["required_fields"])
        self.assertIn(
            "trailing_stop_loss.broker_order.required_before_entry_transmission",
            template["required_fields"],
        )

    def test_configuration_status_reports_watched_scenario(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        self.repository.upsert_setup(setup.to_record())

        status = self.engine.configuration_status()

        self.assertEqual(status["active_configuration"]["loaded_setup_count"], 1)
        self.assertEqual(status["active_configuration"]["watched_setup_count"], 1)
        scenario = status["current_scenario"]
        self.assertEqual(scenario["setup_id"], setup.setup_id)
        self.assertTrue(scenario["enabled"])
        self.assertTrue(scenario["armed"])
        self.assertEqual(scenario["missing_required_parameters"], [])
        self.assertIn("breakout", scenario["awaited_condition"].lower())


if __name__ == "__main__":
    unittest.main()
