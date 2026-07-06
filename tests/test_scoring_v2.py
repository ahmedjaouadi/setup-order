from __future__ import annotations

import unittest
from typing import Any

from app.opportunity_scanner.scoring import (
    QUALITY_COMPONENT_WEIGHTS,
    OpportunityContextScorer,
    compute_quality_score,
    quality_grade,
)

# Snapshot exercising every sub-criterion computable in F1:
# 2 (averages) + 8 (daily) + 6 (intraday) + 7 (rvol) + 4 (atr) + 3 (spread)
# + 2 (liquidity) = 32.
FULL_F1_SNAPSHOT: dict[str, Any] = {
    "price_above_ema20": True,
    "price_above_sma50": True,
    "return_20_bar_pct": 1.5,
    "perf_stock_1d": 4.0,
    "rvol": 1.8,
    "atr_pct": 2.0,
    "spread_pct": 0.2,
    "avg_volume_15m": 500_000,
}
FULL_F1_TOTAL = 32.0


def _component_score(result: dict[str, Any], component: str) -> float:
    return float(result["components"][component]["score"])


def _criteria(result: dict[str, Any], component: str) -> dict[str, float]:
    return result["components"][component]["criteria"]


class SubCriterionIsolationTests(unittest.TestCase):
    """Each sub-criterion triggered by a minimal snapshot (TODO step 8)."""

    def test_above_key_averages(self) -> None:
        result = compute_quality_score({"price_above_ema20": True, "price_above_sma50": True})
        self.assertEqual(_criteria(result, "trend_quality")["above_key_averages"], 2.0)
        self.assertEqual(result["quality_score"], 2.0)

    def test_above_key_averages_needs_both(self) -> None:
        result = compute_quality_score({"price_above_ema20": True, "price_above_sma50": False})
        self.assertEqual(_criteria(result, "trend_quality")["above_key_averages"], 0.0)
        # Computable-but-false is a 0, not an unavailability.
        self.assertNotIn("trend_quality.above_key_averages", result["unavailable"])

    def test_daily_bullish(self) -> None:
        result = compute_quality_score({"return_20_bar_pct": 0.1})
        self.assertEqual(_criteria(result, "trend_quality")["daily_bullish"], 8.0)
        self.assertEqual(compute_quality_score({"return_20_bar_pct": -0.1})["quality_score"], 0.0)

    def test_intraday_aligned(self) -> None:
        result = compute_quality_score({"perf_stock_1d": 0.5})
        self.assertEqual(_criteria(result, "trend_quality")["intraday_aligned"], 6.0)

    def test_rvol_confirmed(self) -> None:
        self.assertEqual(compute_quality_score({"rvol": 1.5})["quality_score"], 7.0)
        self.assertEqual(compute_quality_score({"rvol": 1.49})["quality_score"], 0.0)

    def test_atr_pct_healthy_range(self) -> None:
        self.assertEqual(compute_quality_score({"atr_pct": 2.0})["quality_score"], 4.0)
        self.assertEqual(compute_quality_score({"atr_pct": 0.5})["quality_score"], 4.0)
        self.assertEqual(compute_quality_score({"atr_pct": 5.0})["quality_score"], 4.0)
        self.assertEqual(compute_quality_score({"atr_pct": 5.1})["quality_score"], 0.0)
        self.assertEqual(compute_quality_score({"atr_pct": 0.4})["quality_score"], 0.0)

    def test_atr_range_is_configurable(self) -> None:
        settings = {"opportunity_scanner": {"quality_score": {"atr_pct_range": [1.0, 3.0]}}}
        self.assertEqual(compute_quality_score({"atr_pct": 0.8}, settings)["quality_score"], 0.0)
        self.assertEqual(compute_quality_score({"atr_pct": 2.0}, settings)["quality_score"], 4.0)

    def test_tight_spread(self) -> None:
        self.assertEqual(compute_quality_score({"spread_pct": 0.3})["quality_score"], 3.0)
        self.assertEqual(compute_quality_score({"spread_pct": 0.31})["quality_score"], 0.0)

    def test_liquidity_threshold(self) -> None:
        self.assertEqual(compute_quality_score({"avg_volume_15m": 100_000})["quality_score"], 2.0)
        self.assertEqual(compute_quality_score({"avg_volume_15m": 99_999})["quality_score"], 0.0)


class CompositionTests(unittest.TestCase):
    def test_component_weights_sum_to_100(self) -> None:
        self.assertEqual(sum(QUALITY_COMPONENT_WEIGHTS.values()), 100)

    def test_full_f1_snapshot_sums_every_computable_criterion(self) -> None:
        result = compute_quality_score(FULL_F1_SNAPSHOT)
        self.assertEqual(result["quality_score"], FULL_F1_TOTAL)
        self.assertEqual(_component_score(result, "trend_quality"), 16.0)
        self.assertEqual(_component_score(result, "volume_quality"), 7.0)
        self.assertEqual(_component_score(result, "risk_quality"), 4.0)
        self.assertEqual(_component_score(result, "execution_quality"), 5.0)

    def test_frozen_components_are_zero_in_f1(self) -> None:
        result = compute_quality_score(FULL_F1_SNAPSHOT)
        for component in ("structure_quality", "market_context", "fundamental_context"):
            self.assertEqual(_component_score(result, component), 0.0, component)

    def test_component_scores_never_exceed_their_weight(self) -> None:
        result = compute_quality_score(FULL_F1_SNAPSHOT)
        for name, component in result["components"].items():
            self.assertLessEqual(component["score"], component["max"], name)
            self.assertEqual(component["max"], QUALITY_COMPONENT_WEIGHTS[name], name)

    def test_empty_snapshot_lists_every_unavailable_sub_criterion(self) -> None:
        result = compute_quality_score({})
        self.assertEqual(result["quality_score"], 0.0)
        self.assertEqual(result["score_grade"], "NO_GO")
        for expected in (
            "trend_quality.above_key_averages",
            "trend_quality.daily_bullish",
            "trend_quality.intraday_aligned",
            "trend_quality.higher_lows",
            "structure_quality.levels",
            "volume_quality.rvol_confirmed",
            "risk_quality.atr_pct_healthy",
            "market_context.spy_above_vwap",
            "fundamental_context.catalyst",
            "execution_quality.tight_spread",
            "execution_quality.liquidity",
        ):
            self.assertIn(expected, result["unavailable"])

    def test_computable_criteria_leave_unavailable_list(self) -> None:
        result = compute_quality_score(FULL_F1_SNAPSHOT)
        self.assertNotIn("trend_quality.above_key_averages", result["unavailable"])
        self.assertNotIn("volume_quality.rvol_confirmed", result["unavailable"])
        # F2/F3/external sub-criteria stay listed regardless of the snapshot.
        self.assertIn("trend_quality.higher_lows", result["unavailable"])
        self.assertIn("market_context.spy_above_vwap", result["unavailable"])

    def test_never_raises_on_garbage(self) -> None:
        self.assertEqual(compute_quality_score(None)["quality_score"], 0.0)  # type: ignore[arg-type]
        self.assertEqual(compute_quality_score({"rvol": "abc"})["quality_score"], 0.0)


class GradeBoundaryTests(unittest.TestCase):
    def test_grades_at_the_documented_boundaries(self) -> None:
        self.assertEqual(quality_grade(100.0), "EXCELLENT")
        self.assertEqual(quality_grade(80.0), "EXCELLENT")
        self.assertEqual(quality_grade(79.99), "ACCEPTABLE")
        self.assertEqual(quality_grade(65.0), "ACCEPTABLE")
        self.assertEqual(quality_grade(64.99), "WEAK")
        self.assertEqual(quality_grade(50.0), "WEAK")
        self.assertEqual(quality_grade(49.99), "NO_GO")
        self.assertEqual(quality_grade(0.0), "NO_GO")


class ScorerIntegrationTests(unittest.TestCase):
    def test_score_payload_carries_quality_fields_alongside_legacy_scores(self) -> None:
        payload = OpportunityContextScorer().score(FULL_F1_SNAPSHOT, [])
        # Legacy scores untouched (compatibility phase, TODO step 8).
        self.assertIn("discovery_score", payload)
        self.assertIn("risk_adjusted_score", payload)
        self.assertEqual(payload["quality_score"], FULL_F1_TOTAL)
        self.assertEqual(payload["score_grade"], "NO_GO")
        breakdown = payload["score_breakdown"]
        self.assertIn("components", breakdown)
        self.assertIn("unavailable", breakdown)

    def test_quality_score_never_silences_automatic_warnings(self) -> None:
        # skills.md 9.1: the score never replaces automatic refusals - a wide
        # spread keeps its warning whatever the quality score says.
        snapshot = {**FULL_F1_SNAPSHOT, "spread_pct": 0.9}
        payload = OpportunityContextScorer().score(snapshot, [])
        self.assertIn("SPREAD_TOO_WIDE", payload["warnings"])


if __name__ == "__main__":
    unittest.main()
