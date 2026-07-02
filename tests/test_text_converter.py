from __future__ import annotations

import unittest

from app.setups.breakout_retest import BreakoutRetestSetup
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.setups.text_converter import convert_text_to_setup


DEFAULTS = {
    "app": {"mode": "paper"},
    "risk": {
        "max_position_amount_usd": 250,
        "max_risk_per_trade_usd": 15,
    },
}


class TextConverterTests(unittest.TestCase):
    def test_converts_breakout_retest_text(self) -> None:
        result = convert_text_to_setup(
            symbol="uec",
            text=(
                "Breakout retest apres cloture au-dessus de 14.50, "
                "retest zone 14.10-14.50, stop 13.85, budget 200, "
                "risque 15. Si prix au-dessus 15.30 stop 14.45."
            ),
            defaults=DEFAULTS,
        )

        self.assertTrue(result.ok)
        config = result.config
        self.assertIsNotNone(config)
        self.assertEqual(config["symbol"], "UEC")
        self.assertEqual(config["setup_type"], "breakout_retest")
        self.assertEqual(config["setup_role"], "ENTRY_AND_MANAGEMENT")
        self.assertEqual(config["mode"], "paper")
        self.assertTrue(config["entry"]["enabled"])
        self.assertEqual(config["breakout"]["daily_close_above"], 14.50)
        self.assertEqual(config["retest"]["zone_min"], 14.10)
        self.assertEqual(config["retest"]["zone_max"], 14.50)
        self.assertEqual(config["trailing_stop_loss"]["initial_stop"], 13.85)
        self.assertNotIn("initial_stop_loss", config["risk"])
        self.assertNotIn("protective_stop", config["risk"])
        self.assertEqual(config["risk"]["risk_model"], "TRAILING_STOP_INITIAL_RISK")
        self.assertTrue(config["risk"]["block_entry_if_trailing_stop_missing"])
        self.assertEqual(
            config["management"]["stop_management"]["mode"],
            "TRAILING_STOP_LOSS",
        )
        self.assertEqual(config["risk"]["max_position_amount_usd"], 200)
        self.assertEqual(config["risk"]["max_risk_usd"], 15)
        self.assertTrue(BreakoutRetestSetup(config).validate().valid)

    def test_uses_risk_defaults_when_text_is_short(self) -> None:
        result = convert_text_to_setup(
            symbol="flnc",
            text="Rebond support zone 12.50-13.00, stop 11.65.",
            defaults=DEFAULTS,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.config["setup_type"], "aggressive_rebound")
        self.assertEqual(result.config["risk"]["max_position_amount_usd"], 250)
        self.assertEqual(result.config["risk"]["max_risk_usd"], 15)
        self.assertEqual(
            result.config["risk"]["risk_model"],
            "TRAILING_STOP_INITIAL_RISK",
        )
        self.assertEqual(result.config["trailing_stop_loss"]["initial_stop"], 11.65)

    def test_requires_stop_loss(self) -> None:
        result = convert_text_to_setup(
            symbol="iren",
            text="Breakout retest zone 8.20-8.50 apres cassure 8.50.",
            defaults=DEFAULTS,
        )

        self.assertFalse(result.ok)
        self.assertIn("stop loss", result.errors[0].lower())

    def test_rejects_missing_price_zone_for_breakout_retest(self) -> None:
        result = convert_text_to_setup(
            symbol="nvts",
            text="Breakout retest apres cloture au-dessus de 4.80, stop 4.20.",
            defaults=DEFAULTS,
        )

        self.assertFalse(result.ok)
        self.assertIn("price levels", result.errors[0])

    def test_accepts_pasted_json_setup(self) -> None:
        text = """
        {
          "setup_id": "NOK_20260604_001",
          "symbol": "NOK",
          "enabled": true,
          "mode": "simulation",
          "setup_type": "momentum_breakout",
          "setup_role": "ENTRY_AND_MANAGEMENT",
          "direction": "long",
          "breakout": {
            "resistance": 16.80,
            "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
            "fast_breakout_volume_ratio_min": 1.50,
            "confirmed_breakout_volume_ratio_min": 1.15,
            "confirmed_breakout_hold_bars": 2,
            "confirmed_breakout_timeframe": "15m",
            "close_above_resistance_required": true
          },
          "stale_setup": {
            "rule_type": "PRICE_TOO_FAR_ABOVE_ENTRY",
            "max_distance_percent": 1.50
          },
          "missed_breakout": {
            "retest_zone_min": 16.45,
            "retest_zone_max": 16.65
          },
          "rearm": {
            "new_local_resistance": 16.80,
            "new_trigger": 16.82,
            "new_limit": 16.87
          },
          "entry": {
            "enabled": true,
            "order_type": "STP_LMT",
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
            "maximum_limit_price": 16.87
          },
          "risk": {
            "max_position_amount_usd": 100,
            "max_risk_usd": 15,
            "initial_stop_loss": 15.45
          }
        }
        """

        result = convert_text_to_setup(
            symbol="NOK",
            text=text,
            defaults=DEFAULTS,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.config["setup_id"], "NOK_20260604_001")
        self.assertEqual(result.config["mode"], "paper")
        self.assertEqual(result.config["entry"]["maximum_limit_price"], 16.87)
        self.assertTrue(result.extracted["json_detected"])

    def test_accepts_setup_template_wrapper_json(self) -> None:
        text = """
        {
          "skeleton": {
            "setup_id": "VST_20260614_001",
            "symbol": "VST",
            "enabled": true,
            "mode": "paper",
            "setup_type": "momentum_breakout",
            "setup_role": "ENTRY_AND_MANAGEMENT",
            "direction": "long",
            "timeframes": {
              "signal": "15m",
              "confirmation": "1d"
            },
            "breakout": {
              "resistance": 150.50,
              "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
              "fast_breakout_volume_ratio_min": 1.5,
              "confirmed_breakout_volume_ratio_min": 0.8,
              "confirmed_breakout_hold_bars": 2,
              "confirmed_breakout_timeframe": "15m",
              "close_above_resistance_required": true
            },
            "missed_breakout": {
              "retest_zone_min": 148.80,
              "retest_zone_max": 150.50
            },
            "rearm": {
              "new_local_resistance": 150.50,
              "new_trigger": 150.52,
              "new_limit": 150.57
            },
            "entry": {
              "enabled": true,
              "order_type": "STP_LMT",
              "trigger_offset": 0.02,
              "limit_offset": 0.05,
              "maximum_limit_price": 150.57
            },
            "risk": {
              "max_position_amount_usd": 250,
              "max_risk_usd": 15,
              "initial_stop_loss": 145.80
            },
            "management": {
              "take_profit_mode": "none",
              "stop_management": {
                "mode": "step_based",
                "steps": [
                  {
                    "step": 1,
                    "condition_type": "CANDLE_CLOSE_ABOVE",
                    "timeframe": "15m",
                    "price": 153.00,
                    "action": "KEEP_STOP"
                  }
                ]
              }
            }
          },
          "supported_setup_types": ["momentum_breakout"],
          "required_fields": ["setup_id", "symbol"]
        }
        """

        result = convert_text_to_setup(
            symbol="VST",
            text=text,
            defaults=DEFAULTS,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.config["setup_id"], "VST_20260614_001")
        self.assertEqual(result.config["symbol"], "VST")
        self.assertEqual(result.config["breakout"]["resistance"], 150.5)
        self.assertEqual(result.config["trailing_stop_loss"]["initial_stop"], 145.8)
        self.assertNotIn("initial_stop_loss", result.config["risk"])
        self.assertNotIn("skeleton", result.config)
        self.assertTrue(
            any("template wrapper" in warning for warning in result.warnings)
        )
        self.assertTrue(MomentumBreakoutSetup(result.config).validate().valid)

        inferred_symbol = convert_text_to_setup(
            symbol="",
            text=text,
            defaults=DEFAULTS,
        )

        self.assertTrue(inferred_symbol.ok)
        self.assertEqual(inferred_symbol.config["symbol"], "VST")

    def test_canonicalizes_flat_json_aliases(self) -> None:
        text = """
        {
          "setup_id": "UEC_20260610_001",
          "symbol": "uec",
          "enabled": true,
          "mode": "simulation",
          "setup_type": "breakout_retest",
          "setup_role": "entry_and_management",
          "direction": "LONG",
          "daily_close_above": "14,50",
          "retest_zone_min": "14.10",
          "retest_zone_max": "14.50",
          "entry_order_type": "stp_lmt",
          "trigger_offset": "0.02",
          "limit_offset": "0.05",
          "SL": "13.85",
          "budget": "200",
          "risque": "15"
        }
        """

        result = convert_text_to_setup(
            symbol="UEC",
            text=text,
            defaults=DEFAULTS,
        )

        self.assertTrue(result.ok)
        config = result.config
        self.assertEqual(config["mode"], "paper")
        self.assertEqual(config["setup_role"], "ENTRY_AND_MANAGEMENT")
        self.assertEqual(config["direction"], "long")
        self.assertEqual(config["breakout"]["daily_close_above"], 14.5)
        self.assertEqual(config["retest"]["zone_min"], 14.1)
        self.assertEqual(config["retest"]["zone_max"], 14.5)
        self.assertEqual(config["trailing_stop_loss"]["initial_stop"], 13.85)
        self.assertNotIn("initial_stop_loss", config["risk"])
        self.assertEqual(config["risk"]["max_position_amount_usd"], 200.0)
        self.assertEqual(config["risk"]["max_risk_usd"], 15.0)
        self.assertEqual(config["entry"]["order_type"], "STP_LMT")
        self.assertTrue(BreakoutRetestSetup(config).validate().valid)


if __name__ == "__main__":
    unittest.main()
