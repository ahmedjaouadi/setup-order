from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.opportunity_scanner.outcome_repository import OutcomeRepository
from app.opportunity_scanner.outcome_tracker import (
    OutcomeTracker,
    aggregate_stats,
    evaluate_window,
)
from app.storage.database import Database

FRIDAY = "2026-07-03T14:00:00+00:00"  # 10:00 ET on a Friday
AFTER = "2026-07-13T21:00:00+00:00"  # well past both horizons


def _bars(*triples: tuple[float, float, float]) -> list[dict[str, Any]]:
    return [{"high": h, "low": lo, "close": c} for h, lo, c in triples]


class EvaluateWindowTests(unittest.TestCase):
    """Pure triple-barrier maths on synthetic series."""

    def test_plus_1r_before_minus_1r(self) -> None:
        result = evaluate_window(100.0, 2.0, _bars((103, 99, 102)))
        self.assertEqual(result["label_1r"], 1)
        self.assertEqual(result["mfe_pct"], 3.0)
        self.assertEqual(result["mae_pct"], -1.0)
        self.assertEqual(result["forward_return_pct"], 2.0)

    def test_minus_1r_before_plus_1r(self) -> None:
        result = evaluate_window(100.0, 2.0, _bars((101, 97, 98)))
        self.assertEqual(result["label_1r"], 0)

    def test_neither_barrier_is_null(self) -> None:
        result = evaluate_window(100.0, 2.0, _bars((101, 99, 100.5)))
        self.assertIsNone(result["label_1r"])

    def test_same_bar_both_barriers_is_conservative_zero(self) -> None:
        result = evaluate_window(100.0, 2.0, _bars((105, 95, 101)))
        self.assertEqual(result["label_1r"], 0)
        self.assertEqual(result["mfe_pct"], 5.0)
        self.assertEqual(result["mae_pct"], -5.0)

    def test_barrier_order_across_bars(self) -> None:
        # +1R reached in the first bar, -1R only in a later bar -> win.
        result = evaluate_window(100.0, 2.0, _bars((102, 99, 101), (100, 97, 98)))
        self.assertEqual(result["label_1r"], 1)


class RecordDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "s.sqlite")
        self.database.initialize()
        self.repository = OutcomeRepository(self.database)
        self.tracker = OutcomeTracker(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_creates_one_pending_row_per_horizon(self) -> None:
        created = self.tracker.record_detection(
            "tech_a", "AAPL", {"price": 100, "atr": 3}, now=FRIDAY
        )
        self.assertEqual(len(created), 2)
        rows = self.repository.outcomes_for_technique("tech_a")
        self.assertEqual({row["horizon"] for row in rows}, {"1d", "3d"})
        self.assertTrue(all(row["status"] == "PENDING" for row in rows))
        # r_unit from ATR: 3 / 100 * 100 = 3%.
        self.assertTrue(all(row["r_unit_pct"] == 3.0 for row in rows))

    def test_weekend_shift_pushes_due_to_next_trading_day(self) -> None:
        self.tracker.record_detection("tech_a", "AAPL", {"price": 100}, now=FRIDAY)
        rows = {row["horizon"]: row for row in self.repository.outcomes_for_technique("tech_a")}
        # +1 trading day from Friday 2026-07-03 is Monday 2026-07-06.
        self.assertTrue(rows["1d"]["evaluation_due_at"].startswith("2026-07-06"))
        # +3 trading days lands on Wednesday 2026-07-08.
        self.assertTrue(rows["3d"]["evaluation_due_at"].startswith("2026-07-08"))

    def test_atr_fallback_when_missing(self) -> None:
        self.tracker.record_detection("tech_a", "AAPL", {"price": 100}, now=FRIDAY)
        rows = self.repository.outcomes_for_technique("tech_a")
        self.assertTrue(all(row["r_unit_pct"] == 2.0 for row in rows))
        self.assertTrue(all(row["payload"]["atr_fallback_used"] for row in rows))

    def test_dedup_same_session_same_horizon(self) -> None:
        first = self.tracker.record_detection("tech_a", "AAPL", {"price": 100}, now=FRIDAY)
        second = self.tracker.record_detection("tech_a", "AAPL", {"price": 101}, now=FRIDAY)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        self.assertEqual(len(self.repository.outcomes_for_technique("tech_a")), 2)

    def test_missing_price_records_nothing(self) -> None:
        created = self.tracker.record_detection("tech_a", "AAPL", {"atr": 3}, now=FRIDAY)
        self.assertEqual(created, [])

    def test_stored_snapshot_carries_full_context_tags(self) -> None:
        # skills.md 32.2bis: every outcome must travel with its context tags so
        # the learning engine can slice by time bucket / rvol / spread later.
        self.tracker.record_detection(
            "tech_a",
            "AAPL",
            {"price": 100, "atr": 3, "rvol": 1.5, "spread_pct": 0.05},
            now=FRIDAY,
        )
        rows = self.repository.outcomes_for_technique("tech_a")
        self.assertTrue(rows)
        for row in rows:
            tags = row["features_snapshot"]["context_tags"]
            self.assertEqual(
                set(tags),
                {
                    "time_bucket",
                    "rvol_bucket",
                    "spread_bucket",
                    "day_of_week",
                    "market_regime",
                    "had_catalyst",
                },
            )
            # FRIDAY is 10:00 ET on a Friday with rvol 1.5 and a tight spread.
            self.assertEqual(tags["time_bucket"], "MORNING")
            self.assertEqual(tags["rvol_bucket"], "1.2-2.0")
            self.assertEqual(tags["spread_bucket"], "tight")
            self.assertEqual(tags["day_of_week"], "FRI")
            # The original snapshot fields are preserved alongside the tags.
            self.assertEqual(row["features_snapshot"]["price"], 100)


class EvaluateDueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "s.sqlite")
        self.database.initialize()
        self.repository = OutcomeRepository(self.database)
        self.tracker = OutcomeTracker(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_pending_to_evaluated_with_forward_return(self) -> None:
        self.tracker.record_detection("tech_a", "AAPL", {"price": 100, "atr": 2}, now=FRIDAY)
        result = self.tracker.evaluate_due(
            lambda symbol, start, end: _bars((103, 99, 102)), now=AFTER
        )
        self.assertEqual(result["evaluated"], 2)
        rows = self.repository.outcomes_for_technique("tech_a", status="EVALUATED")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["forward_return_pct"] == 2.0 for row in rows))
        self.assertTrue(all(row["label_1r"] == 1 for row in rows))

    def test_missing_data_expires_without_crash(self) -> None:
        self.tracker.record_detection("tech_a", "AAPL", {"price": 100}, now=FRIDAY)
        result = self.tracker.evaluate_due(lambda symbol, start, end: [], now=AFTER)
        self.assertEqual(result["expired"], 2)
        rows = self.repository.outcomes_for_technique("tech_a")
        self.assertTrue(all(row["status"] == "EXPIRED" for row in rows))

    def test_not_due_yet_is_left_pending(self) -> None:
        self.tracker.record_detection("tech_a", "AAPL", {"price": 100}, now=FRIDAY)
        # Evaluate at a time before the 1d horizon is due.
        result = self.tracker.evaluate_due(
            lambda symbol, start, end: _bars((103, 99, 102)),
            now="2026-07-03T15:00:00+00:00",
        )
        self.assertEqual(result["evaluated"], 0)


class AggregateStatsTests(unittest.TestCase):
    def test_warmup_below_min_samples(self) -> None:
        rows = [
            {"label_1r": 1, "forward_return_pct": 2.0, "mfe_pct": 3.0, "mae_pct": -1.0},
            {"label_1r": 0, "forward_return_pct": -1.5, "mfe_pct": 0.5, "mae_pct": -2.0},
        ]
        stats = aggregate_stats(rows, min_samples=30)
        self.assertEqual(stats["sample_size"], 2)
        self.assertEqual(stats["hit_rate"], 0.5)
        self.assertEqual(stats["status_label"], "WARMUP")
        # expectancy_r = 0.5*avg(win mfe=3.0) - 0.5*avg(loss |mae|=2.0) = 0.5.
        self.assertEqual(stats["expectancy_r"], 0.5)

    def test_ready_at_min_samples(self) -> None:
        rows = [
            {"label_1r": 1, "forward_return_pct": 1.0, "mfe_pct": 1.0, "mae_pct": -0.5}
            for _ in range(30)
        ]
        stats = aggregate_stats(rows, min_samples=30)
        self.assertEqual(stats["status_label"], "READY")
        self.assertEqual(stats["hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
