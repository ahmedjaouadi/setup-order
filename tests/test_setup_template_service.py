from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from app.engine.setup_template_service import SetupTemplateService
from app.settings import DEFAULT_CONFIG, Settings


class SetupTemplateServiceTests(unittest.TestCase):
    def test_builds_setup_template_from_settings(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        config["app"]["mode"] = "paper"
        config["setup_defaults"]["entry"]["trigger_offset"] = 0.03
        config["orders"]["default_entry_order_type"] = "STP_LMT"
        settings = Settings.from_dict(config)

        template = SetupTemplateService(settings).setup_config_template()

        self.assertEqual(template["template_type"], "universal")
        self.assertEqual(template["skeleton"]["mode"], "paper")
        self.assertEqual(template["skeleton"]["schema_version"], "2.0.0")
        self.assertEqual(template["skeleton"]["setup_type"], "CHOOSE_ONE_SETUP_TYPE")
        self.assertEqual(template["skeleton"]["setup_role"], "AUTO_SELECT")
        self.assertIn("momentum_breakout", template["supported_setup_types"])
        self.assertIn("trailing_runner", template["supported_setup_types"])
        self.assertIn("trailing_runner", template["skeleton"]["setup_type_options"])
        self.assertIn("trailing_runner", template["skeleton"]["setup_type_selection_guide"])
        self.assertEqual(
            template["skeleton"]["entry"]["order_type"],
            "AUTO_SELECT",
        )
        self.assertEqual(
            template["skeleton"]["entry"]["trigger_offset"],
            0.03,
        )
        self.assertEqual(template["skeleton"]["entry"]["enabled"], "AUTO_SELECT")
        self.assertEqual(template["skeleton"]["volume_confirmation"]["enabled"], "AUTO_SELECT")
        self.assertIn("volume_confirmation", template["skeleton"])
        self.assertIn("volume_confirmation_policy_by_setup_type", template["skeleton"])
        self.assertIn("trailing_stop_loss", template["skeleton"])
        self.assertTrue(template["skeleton"]["trailing_stop_loss"]["enabled"])
        self.assertIsNone(template["skeleton"]["trailing_stop_loss"]["current_stop"])
        self.assertEqual(
            template["skeleton"]["trailing_stop_loss"]["stop_source"],
            "AUTO_CALCULATED",
        )
        self.assertEqual(
            template["skeleton"]["trailing_stop_loss"]["calculation"]["method"],
            "HYBRID_ATR_STRUCTURE",
        )
        self.assertEqual(
            template["skeleton"]["trailing_stop_loss"]["calculation"]["atr"]["timeframe"],
            "1h",
        )
        self.assertTrue(
            template["skeleton"]["trailing_stop_loss"]["activation"]["activate_before_entry_transmission"]
        )
        self.assertTrue(
            template["skeleton"]["trailing_stop_loss"]["broker_order"]["required_before_entry_transmission"]
        )
        self.assertEqual(
            template["skeleton"]["risk"]["risk_model"],
            "TRAILING_STOP_INITIAL_RISK",
        )
        self.assertTrue(template["skeleton"]["risk"]["block_entry_if_risk_unknown"])
        self.assertTrue(template["skeleton"]["risk"]["block_entry_if_trailing_stop_missing"])
        self.assertNotIn("initial_stop_loss", template["skeleton"]["risk"])
        self.assertNotIn("protective_stop", template["skeleton"]["risk"])
        self.assertEqual(
            template["skeleton"]["management"]["stop_management"]["mode"],
            "TRAILING_STOP_LOSS",
        )
        self.assertEqual(
            template["skeleton"]["management"]["stop_management"]["source"],
            "trailing_stop_loss",
        )
        self.assertTrue(
            template["skeleton"]["volume_confirmation_policy_by_setup_type"]["momentum_breakout"]["required_for_entry"]
        )
        self.assertEqual(
            template["skeleton"]["volume_confirmation_policy_by_setup_type"]["position_management"]["weak_volume_action"],
            "IGNORE_FOR_MANAGEMENT",
        )
        self.assertIn("retest", template["skeleton"])
        self.assertIn("support_zone", template["skeleton"])
        self.assertIn("do not default to momentum_breakout", template["skeleton"]["_template"]["selection_rules"])
        self.assertEqual(
            template["skeleton"]["_template"]["template_kind"],
            "UNIVERSAL_SETUP_REQUEST",
        )
        self.assertEqual(
            template["skeleton"]["expected_output"]["format"],
            "FINAL_CANONICAL_SETUP_ONLY",
        )
        self.assertIn(
            "replace CHOOSE_ONE_SETUP_TYPE with the selected setup_type",
            template["skeleton"]["expected_output"]["rules"],
        )
        self.assertIn(
            "all final setups must include trailing_stop_loss.enabled=true",
            template["skeleton"]["expected_output"]["rules"],
        )
        self.assertIn(
            "entry orders cannot be transmitted unless trailing stop broker order is ready",
            template["skeleton"]["expected_output"]["rules"],
        )
        self.assertIn(
            "do not use fixed stop-loss as the main protection model",
            template["skeleton"]["expected_output"]["rules"],
        )
        self.assertIn(
            "never generate an initial BUY order for position_management, runner, or trailing_runner",
            template["skeleton"]["_template"]["selection_rules"],
        )
        self.assertIn(
            "all management setups require trailing_stop_loss.enabled=true",
            template["skeleton"]["_template"]["selection_rules"],
        )
        self.assertIn(
            "breakout.resistance",
            template["required_by_setup_type"]["momentum_breakout"],
        )
        self.assertIn(
            "trailing_stop_loss.enabled=true",
            template["required_by_setup_type"]["momentum_breakout"],
        )
        self.assertIn(
            "trailing_stop_loss.broker_order.required_before_entry_transmission=true",
            template["required_by_setup_type"]["momentum_breakout"],
        )
        self.assertIn("trailing_stop_loss.initial_stop", template["required_fields"])
        self.assertIn(
            "trailing_stop_loss.broker_order.required_before_entry_transmission",
            template["required_fields"],
        )
        self.assertEqual(
            template["expected_output"]["format"],
            "FINAL_CANONICAL_SETUP_ONLY",
        )

    def test_builds_specific_momentum_breakout_template(self) -> None:
        config = deepcopy(DEFAULT_CONFIG)
        config["app"]["mode"] = "paper"
        config["setup_defaults"]["entry"]["trigger_offset"] = 0.03
        config["orders"]["default_entry_order_type"] = "STP_LMT"
        settings = Settings.from_dict(config)

        result = SetupTemplateService(settings).momentum_breakout_template()
        template = result["template"]

        self.assertEqual(result["template_type"], "momentum_breakout")
        self.assertEqual(template["setup_type"], "momentum_breakout")
        self.assertEqual(template["setup_role"], "ENTRY_AND_MANAGEMENT")
        self.assertTrue(template["entry"]["enabled"])
        self.assertEqual(template["entry"]["order_type"], "STP_LMT")
        self.assertEqual(template["entry"]["trigger_offset"], 0.03)
        self.assertIsNone(template["breakout"]["resistance"])
        self.assertIsNone(template["entry"]["maximum_limit_price"])
        self.assertNotIn("initial_stop_loss", template["risk"])
        self.assertNotIn("protective_stop", template["risk"])
        self.assertEqual(template["risk"]["risk_model"], "TRAILING_STOP_INITIAL_RISK")
        self.assertTrue(template["risk"]["block_entry_if_trailing_stop_missing"])
        self.assertIn("trailing_stop_loss", template)
        self.assertTrue(template["trailing_stop_loss"]["enabled"])
        self.assertIsNone(template["trailing_stop_loss"]["initial_stop"])
        self.assertEqual(
            template["management"]["stop_management"]["mode"],
            "TRAILING_STOP_LOSS",
        )
        self.assertEqual(
            template["management"]["stop_management"]["source"],
            "trailing_stop_loss",
        )
        self.assertIn("volume_confirmation", template)
        self.assertIn("missed_breakout", template)
        self.assertIn("rearm", template)
        self.assertEqual(
            result["required_fields_before_arm"],
            [
                "breakout.resistance",
                "entry.maximum_limit_price",
                "trailing_stop_loss.initial_stop",
                "trailing_stop_loss.broker_order.required_before_entry_transmission",
            ],
        )

    def test_base_schema_exposes_trailing_stop_loss_as_primary_protection_model(self) -> None:
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "schemas"
            / "setup.base.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        risk_properties = schema["properties"]["risk"]["properties"]
        self.assertIn("max_position_amount_usd", risk_properties)
        self.assertIn("max_risk_usd", risk_properties)
        self.assertIn("emergency_exit_if_stop_fails", risk_properties)
        self.assertNotIn("initial_stop_loss", risk_properties)
        self.assertNotIn("protective_stop", risk_properties)

        trailing_properties = schema["properties"]["trailing_stop_loss"]["properties"]
        self.assertIn("activation", trailing_properties)
        self.assertIn("calculation", trailing_properties)
        self.assertIn("ratchet_rules", trailing_properties)
        self.assertIn("broker_order", trailing_properties)
        broker_order = trailing_properties["broker_order"]["properties"]
        self.assertIn("required_before_entry_transmission", broker_order)
        self.assertIn("use_native_ibkr_trailing_order_if_available", broker_order)


if __name__ == "__main__":
    unittest.main()
