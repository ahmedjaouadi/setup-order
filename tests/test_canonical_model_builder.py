from __future__ import annotations

import unittest

from app.conversion import canonicalize_setup_config


class CanonicalModelBuilderTests(unittest.TestCase):
    def test_maps_aliases_and_promotes_legacy_mode(self) -> None:
        result = canonicalize_setup_config(
            {
                "setup_id": "UEC_20260610_001",
                "symbol": "uec",
                "enabled": "true",
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
                "risque": "15",
            },
            defaults={"app": {"mode": "paper"}},
        )

        config = result.config
        self.assertEqual(config["symbol"], "UEC")
        self.assertEqual(config["mode"], "paper")
        self.assertEqual(config["setup_role"], "ENTRY_AND_MANAGEMENT")
        self.assertEqual(config["direction"], "long")
        self.assertEqual(config["breakout"]["daily_close_above"], 14.5)
        self.assertEqual(config["retest"]["zone_min"], 14.1)
        self.assertEqual(config["retest"]["zone_max"], 14.5)
        self.assertEqual(config["trailing_stop_loss"]["initial_stop"], 13.85)
        self.assertEqual(
            config["trailing_stop_loss"]["calculation"]["method"],
            "HYBRID_ATR_STRUCTURE",
        )
        self.assertTrue(
            config["trailing_stop_loss"]["broker_order"]["required_before_entry_transmission"]
        )
        self.assertTrue(
            config["trailing_stop_loss"]["broker_order"]["use_native_ibkr_trailing_order_if_available"]
        )
        self.assertNotIn("initial_stop_loss", config["risk"])
        self.assertNotIn("protective_stop", config["risk"])
        self.assertEqual(config["risk"]["max_position_amount_usd"], 200.0)
        self.assertEqual(config["risk"]["max_risk_usd"], 15.0)
        self.assertEqual(config["entry"]["order_type"], "STP_LMT")
        self.assertIn(
            "Legacy simulation mode was promoted to paper.",
            result.warnings,
        )

    def test_preserves_management_protective_stop(self) -> None:
        result = canonicalize_setup_config(
            {
                "setup_id": "TEST_POSITION_MANAGEMENT_001",
                "symbol": "test",
                "mode": "paper",
                "setup_type": "position_management",
                "setup_role": "management_only",
                "entry": {"enabled": False},
                "risk": {"protective_stop": "9.50"},
            }
        )

        config = result.config
        self.assertEqual(config["setup_role"], "MANAGEMENT_ONLY")
        self.assertEqual(config["trailing_stop_loss"]["initial_stop"], 9.5)
        self.assertNotIn("protective_stop", config["risk"])
        self.assertNotIn("initial_stop_loss", config["risk"])

    def test_enriches_partial_trailing_stop_to_full_intelligent_defaults(self) -> None:
        result = canonicalize_setup_config(
            {
                "setup_id": "TEST_TRAILING_001",
                "symbol": "test",
                "mode": "paper",
                "setup_type": "momentum_breakout",
                "setup_role": "entry_and_management",
                "direction": "long",
                "entry": {"enabled": True, "order_type": "stp_lmt"},
                "risk": {
                    "max_position_amount_usd": 250,
                    "max_risk_usd": 15,
                },
                "trailing_stop_loss": {
                    "enabled": True,
                    "initial_stop": "13.40",
                },
            }
        )

        trailing = result.config["trailing_stop_loss"]
        self.assertEqual(trailing["mode"], "AUTO_INTELLIGENT")
        self.assertEqual(trailing["initial_stop"], 13.4)
        self.assertEqual(trailing["activation"]["mode"], "ON_ENTRY_FILL")
        self.assertEqual(trailing["calculation"]["method"], "HYBRID_ATR_STRUCTURE")
        self.assertTrue(trailing["ratchet_rules"]["do_not_update_if_spread_wide"])
        self.assertTrue(
            trailing["broker_order"]["fallback_to_managed_stop_updates"]
        )

    def test_adds_root_trailing_stop_loss_when_missing(self) -> None:
        result = canonicalize_setup_config(
            {
                "setup_id": "TEST_MISSING_TRAILING_001",
                "symbol": "test",
                "mode": "paper",
                "setup_type": "momentum_breakout",
                "setup_role": "entry_and_management",
                "direction": "long",
                "entry": {"enabled": True, "order_type": "stp_lmt"},
                "risk": {
                    "max_position_amount_usd": 250,
                    "max_risk_usd": 15,
                },
            }
        )

        trailing = result.config["trailing_stop_loss"]
        self.assertTrue(trailing["enabled"])
        self.assertIsNone(trailing["initial_stop"])
        self.assertEqual(trailing["stop_source"], "AUTO_CALCULATED")


if __name__ == "__main__":
    unittest.main()
