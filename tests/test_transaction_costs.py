from __future__ import annotations

import unittest

from app.engine.trade_guards import REASON_RISK_TOO_HIGH, STATUS_NO_GO
from app.engine.transaction_costs import (
    COST_GATE_NO_GO,
    COST_GATE_OK,
    COST_GATE_WARNING,
    evaluate_cost_gate,
    simulated_fill_price,
)

SETTINGS = {
    "trade_guards": {
        "transaction_costs": {
            "enabled": True,
            "commission_per_order_usd": 1.0,
            "commission_per_share_usd": 0.005,
            "regulatory_fees_per_order_usd": 0.05,
            "expected_slippage_per_share_usd": 0.01,
            "warn_cost_to_risk_ratio": 0.15,
            "max_cost_to_risk_ratio": 0.30,
            "simulated_fill_min_slippage_usd": 0.01,
            "simulated_fill_spread_fraction": 0.5,
        },
    },
}


class CostGateTests(unittest.TestCase):
    def test_cheap_trade_passes(self) -> None:
        gate = evaluate_cost_gate(
            quantity=10,
            spread=0.01,
            max_risk_usd=100.0,
            settings=SETTINGS,
        )
        self.assertEqual(gate["gate"], COST_GATE_OK)
        self.assertIsNone(gate["reason_code"])

    def test_warning_between_15_and_30_percent(self) -> None:
        # Round trip: commissions 2*(1+0.05)=2.1 + fees 0.1 + slippage 0.2 +
        # spread 0.1 = 2.5 USD -> 16.7% of a 15 USD risk.
        gate = evaluate_cost_gate(
            quantity=10,
            spread=0.01,
            max_risk_usd=15.0,
            settings=SETTINGS,
        )
        self.assertEqual(gate["gate"], COST_GATE_WARNING)
        self.assertGreater(gate["cost_to_risk_ratio"], 0.15)
        self.assertLessEqual(gate["cost_to_risk_ratio"], 0.30)

    def test_no_go_above_30_percent(self) -> None:
        gate = evaluate_cost_gate(
            quantity=50,
            spread=0.05,
            max_risk_usd=15.0,
            settings=SETTINGS,
        )
        self.assertEqual(gate["gate"], COST_GATE_NO_GO)
        self.assertEqual(gate["status"], STATUS_NO_GO)
        self.assertEqual(gate["reason_code"], REASON_RISK_TOO_HIGH)

    def test_disabled_gate_always_ok(self) -> None:
        settings = {"trade_guards": {"transaction_costs": {"enabled": False}}}
        gate = evaluate_cost_gate(
            quantity=1000,
            spread=1.0,
            max_risk_usd=1.0,
            settings=settings,
        )
        self.assertEqual(gate["gate"], COST_GATE_OK)

    def test_zero_risk_skips_ratio(self) -> None:
        gate = evaluate_cost_gate(
            quantity=10,
            spread=0.01,
            max_risk_usd=0.0,
            settings=SETTINGS,
        )
        self.assertEqual(gate["gate"], COST_GATE_OK)
        self.assertIsNone(gate["cost_to_risk_ratio"])


class SimulatedFillTests(unittest.TestCase):
    def test_long_fill_never_perfect(self) -> None:
        fill = simulated_fill_price(
            trigger_price=20.50,
            spread=None,
            settings=SETTINGS,
        )
        self.assertEqual(fill, 20.51)

    def test_long_fill_uses_half_spread_when_larger(self) -> None:
        fill = simulated_fill_price(
            trigger_price=20.50,
            spread=0.10,
            settings=SETTINGS,
        )
        self.assertEqual(fill, 20.55)

    def test_short_fill_slips_down(self) -> None:
        fill = simulated_fill_price(
            trigger_price=20.50,
            spread=0.10,
            settings=SETTINGS,
            direction="short",
        )
        self.assertEqual(fill, 20.45)


if __name__ == "__main__":
    unittest.main()
