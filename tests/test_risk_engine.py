from __future__ import annotations

import unittest

from app.engine.risk_engine import (
    RiskEngine,
    RiskLimits,
    calculate_initial_trailing_stop_long,
    calculate_position_size,
    can_transmit_entry_order,
    migrate_legacy_stop_to_trailing_stop,
    ratchet_trailing_stop,
    update_trailing_stop_long,
    validate_trailing_stop_required,
)


class RiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine(
            RiskLimits(
                max_open_positions=5,
                max_position_amount_usd=250,
                max_risk_per_trade_usd=15,
                max_daily_loss_usd=50,
                max_total_exposure_usd=1000,
                allow_short=False,
            )
        )
        self.setup = {
            "direction": "long",
            "risk": {
                "max_position_amount_usd": 200,
                "max_risk_usd": 15,
            },
            "trailing_stop_loss": {
                "enabled": True,
                "initial_stop": 13.85,
                "broker_order": {
                    "required_before_entry_transmission": True,
                    "trailing_stop_order_ready": True,
                },
            },
        }

    def test_approves_quantity_from_budget_and_risk(self) -> None:
        decision = self.engine.evaluate(
            setup_config=self.setup,
            entry_price=14.52,
            stop_loss=13.85,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.quantity, 13)
        self.assertLessEqual(decision.risk_amount_usd, 15)

    def test_stp_lmt_uses_limit_as_worst_case_entry_price(self) -> None:
        setup = {
            "direction": "long",
            "entry": {"order_type": "STP_LMT", "limit_offset": 0.50},
            "risk": {
                "max_position_amount_usd": 1000,
                "max_risk_usd": 10,
            },
            "trailing_stop_loss": {
                "enabled": True,
                "initial_stop": 9.50,
                "broker_order": {
                    "required_before_entry_transmission": True,
                    "trailing_stop_order_ready": True,
                },
            },
        }

        decision = self.engine.evaluate(
            setup_config=setup,
            entry_price=10.00,
            stop_loss=9.50,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.trigger_price, 10.00)
        self.assertEqual(decision.entry_price, 10.50)
        self.assertEqual(decision.quantity, 10)
        self.assertEqual(decision.risk_amount_usd, 10.00)

    def test_rejects_stop_above_entry_for_long(self) -> None:
        self.setup["trailing_stop_loss"]["initial_stop"] = 15.00
        decision = self.engine.evaluate(
            setup_config=self.setup,
            entry_price=14.50,
            stop_loss=15.00,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertFalse(decision.approved)
        self.assertIn("stop loss", decision.reason.lower())

    def test_blocks_when_trailing_stop_initial_is_not_ready(self) -> None:
        setup = {
            "direction": "long",
            "risk": {
                "max_position_amount_usd": 200,
                "max_risk_usd": 15,
            },
            "trailing_stop_loss": {
                "enabled": True,
                "initial_stop": None,
            },
        }

        decision = self.engine.evaluate(
            setup_config=setup,
            entry_price=14.50,
            stop_loss=0,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "BLOCKED_TRAILING_STOP_NOT_READY")

    def test_blocks_when_trailing_stop_broker_order_is_not_ready(self) -> None:
        self.setup["trailing_stop_loss"]["broker_order"].pop(
            "trailing_stop_order_ready",
            None,
        )

        decision = self.engine.evaluate(
            setup_config=self.setup,
            entry_price=14.50,
            stop_loss=13.85,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "BLOCKED_TRAILING_STOP_NOT_READY")

    def test_requires_trailing_stop_for_new_setup_without_legacy_stop(self) -> None:
        setup = {
            "direction": "long",
            "risk": {
                "max_position_amount_usd": 200,
                "max_risk_usd": 15,
            },
        }

        decision = self.engine.evaluate(
            setup_config=setup,
            entry_price=14.50,
            stop_loss=0,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "TRAILING_STOP_LOSS_REQUIRED")

    def test_does_not_use_legacy_stop_as_direct_risk_fallback(self) -> None:
        setup = {
            "direction": "long",
            "risk": {
                "max_position_amount_usd": 200,
                "max_risk_usd": 15,
                "initial_stop_loss": 13.85,
            },
        }

        decision = self.engine.evaluate(
            setup_config=setup,
            entry_price=14.50,
            stop_loss=13.85,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "TRAILING_STOP_LOSS_REQUIRED")

    def test_trailing_stop_never_lowers_for_long(self) -> None:
        stop, event = ratchet_trailing_stop(
            current_stop=19.45,
            new_calculated_stop=19.20,
        )

        self.assertEqual(stop, 19.45)
        self.assertEqual(event, "STOP_NOT_LOWERED")

    def test_trailing_stop_can_raise_for_long(self) -> None:
        stop, event = ratchet_trailing_stop(
            current_stop=19.45,
            new_calculated_stop=20.35,
        )

        self.assertEqual(stop, 20.35)
        self.assertEqual(event, "TRAILING_STOP_RAISED")

    def test_validate_trailing_stop_required_reports_missing_initial_stop(self) -> None:
        errors = validate_trailing_stop_required(
            {
                "setup_role": "ENTRY_AND_MANAGEMENT",
                "entry": {"enabled": True},
                "trailing_stop_loss": {
                    "enabled": True,
                    "initial_stop": None,
                    "broker_order": {
                        "required_before_entry_transmission": True,
                    },
                },
            }
        )

        self.assertEqual(errors, ["TRAILING_STOP_INITIAL_STOP_REQUIRED_BEFORE_ARMING"])

    def test_can_transmit_entry_order_requires_broker_ready_trailing_stop(self) -> None:
        allowed, reasons = can_transmit_entry_order(
            self.setup,
            {
                "trailing_stop_order_ready": False,
                "broker_tracker_status": "OK",
                "tws_connected": True,
            },
        )

        self.assertFalse(allowed)
        self.assertEqual(reasons, ["TRAILING_STOP_BROKER_ORDER_NOT_READY"])

    def test_calculate_position_size_uses_trailing_initial_stop(self) -> None:
        result = calculate_position_size(
            "long",
            worst_case_entry_price=10.50,
            trailing_initial_stop=9.50,
            max_risk_usd=10,
            max_position_amount_usd=1000,
        )

        self.assertEqual(result["maximum_quantity"], 10)
        self.assertEqual(result["risk_per_share"], 1.0)
        self.assertEqual(result["status"], "OK")

    def test_calculates_initial_trailing_stop_with_hybrid_structure(self) -> None:
        result = calculate_initial_trailing_stop_long(
            entry_price=20.00,
            atr_1h=1.00,
            support_level=18.50,
            spread=0.03,
            tick_size=0.01,
            volatility_regime="NORMAL",
        )

        self.assertEqual(result["method_used"], "HYBRID_ATR_STRUCTURE")
        self.assertEqual(result["initial_stop"], 18.2)

    def test_update_trailing_stop_long_never_lowers(self) -> None:
        result = update_trailing_stop_long(
            current_stop=19.45,
            new_calculated_stop=19.20,
        )

        self.assertFalse(result["updated"])
        self.assertEqual(result["final_stop"], 19.45)
        self.assertEqual(result["event"], "STOP_NOT_LOWERED")

    def test_migrates_legacy_stop_to_trailing_stop(self) -> None:
        setup = {
            "risk": {
                "initial_stop_loss": 9.50,
                "protective_stop": 9.50,
            }
        }

        migrated = migrate_legacy_stop_to_trailing_stop(setup)

        self.assertEqual(migrated["trailing_stop_loss"]["initial_stop"], 9.50)
        self.assertEqual(
            migrated["migration_status"],
            "LEGACY_STOP_MIGRATED_TO_TRAILING_STOP",
        )
        self.assertNotIn("initial_stop_loss", migrated["risk"])
        self.assertNotIn("protective_stop", migrated["risk"])

    def test_blocks_after_daily_loss_limit(self) -> None:
        decision = self.engine.evaluate(
            setup_config=self.setup,
            entry_price=14.50,
            stop_loss=13.85,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=-50,
        )

        self.assertFalse(decision.approved)
        self.assertIn("daily loss", decision.reason.lower())

    def test_rejects_zero_quantity(self) -> None:
        setup = {
            "direction": "long",
            "risk": {
                "max_position_amount_usd": 10,
                "max_risk_usd": 1,
            },
            "trailing_stop_loss": {
                "enabled": True,
                "initial_stop": 13.85,
                "broker_order": {
                    "required_before_entry_transmission": True,
                    "trailing_stop_order_ready": True,
                },
            },
        }

        decision = self.engine.evaluate(
            setup_config=setup,
            entry_price=100,
            stop_loss=90,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertFalse(decision.approved)
        self.assertIn("zero", decision.reason.lower())

    def test_rejects_management_only_entry(self) -> None:
        setup = {
            "setup_type": "position_management",
            "setup_role": "MANAGEMENT_ONLY",
            "entry": {"enabled": False},
            "direction": "long",
            "risk": {"protective_stop": 9.50},
        }

        decision = self.engine.evaluate(
            setup_config=setup,
            entry_price=10.00,
            stop_loss=9.50,
            open_positions=0,
            current_exposure_usd=0,
            daily_pnl_usd=0,
        )

        self.assertFalse(decision.approved)
        self.assertIn("MANAGEMENT_ONLY", decision.reason)

    def test_market_data_guard_rejects_inverted_bid_ask(self) -> None:
        decision = self.engine.evaluate_market_data({"bid": 10.05, "ask": 10.00})

        self.assertFalse(decision.approved)
        self.assertIn("Bid price", decision.reason)


if __name__ == "__main__":
    unittest.main()
