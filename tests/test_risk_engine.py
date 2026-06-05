from __future__ import annotations

import unittest

from app.engine.risk_engine import RiskEngine, RiskLimits


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
                "initial_stop_loss": 13.85,
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
                "initial_stop_loss": 9.50,
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
                "initial_stop_loss": 13.85,
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


if __name__ == "__main__":
    unittest.main()
