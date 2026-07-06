from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.api import routes_opportunities, routes_techniques
from app.background_jobs import _record_stale_outcomes_event, _stale_pending_outcomes
from app.opportunity_scanner.outcome_repository import OutcomeRepository
from app.opportunity_scanner.outcome_tracker import OutcomeTracker
from app.storage.database import Database

FRIDAY = "2026-07-03T14:00:00+00:00"  # 10:00 ET on a Friday
AFTER = "2026-07-13T21:00:00+00:00"  # well past both horizons


def _bars(*triples: tuple[float, float, float]) -> list[dict[str, Any]]:
    return [{"high": h, "low": lo, "close": c} for h, lo, c in triples]


class ScanReliabilityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "s.sqlite")
        self.database.initialize()
        self.repository = OutcomeRepository(self.database)
        self.tracker = OutcomeTracker(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()


class ReliabilitySummaryTests(ScanReliabilityTestCase):
    """Etape 13.2: correct/wrong/pending counters per technique and global."""

    def _seed_mixed_outcomes(self) -> None:
        # tech_win: +1R reached -> 2 correct (1d + 3d horizons).
        self.tracker.record_detection("tech_win", "AAPL", {"price": 100, "atr": 2}, now=FRIDAY)
        # tech_lose: -1R first -> 2 wrong.
        self.tracker.record_detection("tech_lose", "MSFT", {"price": 100, "atr": 2}, now=FRIDAY)
        # tech_pending: recorded but never due within the evaluation call.
        self.tracker.record_detection(
            "tech_pending", "NVDA", {"price": 100, "atr": 2}, now="2026-07-10T14:00:00+00:00"
        )

        def provider(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
            if symbol == "AAPL":
                return _bars((103, 99, 102))
            if symbol == "MSFT":
                return _bars((101, 97, 98))
            return []

        self.tracker.evaluate_due(provider, now="2026-07-09T21:00:00+00:00")

    def test_summary_counts_correct_wrong_pending_and_global(self) -> None:
        self._seed_mixed_outcomes()

        summary = self.tracker.reliability_summary()

        by_id = {row["technique_id"]: row for row in summary["techniques"]}
        self.assertEqual(by_id["tech_win"]["correct"], 2)
        self.assertEqual(by_id["tech_win"]["wrong"], 0)
        self.assertEqual(by_id["tech_win"]["hit_rate"], 1.0)
        self.assertFalse(by_id["tech_win"]["min_samples_reached"])
        self.assertEqual(by_id["tech_lose"]["correct"], 0)
        self.assertEqual(by_id["tech_lose"]["wrong"], 2)
        self.assertEqual(by_id["tech_pending"]["pending"], 2)
        self.assertEqual(by_id["tech_pending"]["evaluated"], 0)

        global_stats = summary["global"]
        self.assertEqual(global_stats["detections_total"], 6)
        self.assertEqual(global_stats["correct"], 2)
        self.assertEqual(global_stats["wrong"], 2)
        self.assertEqual(global_stats["pending"], 2)
        self.assertEqual(global_stats["hit_rate"], 0.5)
        self.assertEqual(summary["min_samples"], self.tracker.min_samples)

    def test_stats_api_route_uses_tracker_summary(self) -> None:
        self._seed_mixed_outcomes()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(outcome_tracker=self.tracker))
        )

        import asyncio

        summary = asyncio.run(routes_techniques.techniques_reliability_stats(request))

        self.assertEqual(summary["global"]["correct"], 2)


class OpportunityOutcomeLinkTests(ScanReliabilityTestCase):
    """Etape 13.4: an outcome carries the opportunity that produced it."""

    def test_opportunity_id_is_propagated_and_queryable(self) -> None:
        self.tracker.record_matches(
            ["tech_a"],
            "AAPL",
            {"price": 100, "atr": 2},
            now=FRIDAY,
            opportunity_id="opp_AAPL_1",
        )

        rows = self.repository.outcomes_for_opportunity("opp_AAPL_1")

        self.assertEqual(len(rows), 2)  # one per horizon
        self.assertTrue(all(row["opportunity_id"] == "opp_AAPL_1" for row in rows))
        self.assertTrue(all(row["status"] == "PENDING" for row in rows))

    def test_verdict_visible_after_evaluation(self) -> None:
        self.tracker.record_matches(
            ["tech_a"],
            "AAPL",
            {"price": 100, "atr": 2},
            now=FRIDAY,
            opportunity_id="opp_AAPL_1",
        )
        self.tracker.evaluate_due(lambda s, a, b: _bars((103, 99, 102)), now=AFTER)

        rows = self.repository.outcomes_for_opportunity("opp_AAPL_1")

        self.assertTrue(all(row["status"] == "EVALUATED" for row in rows))
        self.assertTrue(all(row["label_1r"] == 1 for row in rows))
        self.assertTrue(all(row["mfe_pct"] is not None for row in rows))

    def test_opportunity_outcomes_route(self) -> None:
        self.tracker.record_matches(
            ["tech_a"],
            "AAPL",
            {"price": 100, "atr": 2},
            now=FRIDAY,
            opportunity_id="opp_AAPL_1",
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(outcome_tracker=self.tracker))
        )

        import asyncio

        result = asyncio.run(routes_opportunities.opportunity_outcomes(request, "opp_AAPL_1"))

        self.assertEqual(len(result["items"]), 2)


class StaleCollectionDetectionTests(ScanReliabilityTestCase):
    """Etape 13.1: a silent evaluation outage must raise an alarm."""

    def test_overdue_pending_outcomes_are_counted_as_stale(self) -> None:
        self.tracker.record_detection("tech_a", "AAPL", {"price": 100}, now=FRIDAY)

        # Due dates are 2026-07-06 / 2026-07-08; cutoff far in the future.
        self.assertEqual(self.repository.stale_pending_count("2026-08-01T00:00:00+00:00"), 2)
        # Nothing is stale before the due dates.
        self.assertEqual(self.repository.stale_pending_count("2026-07-01T00:00:00+00:00"), 0)

    def test_evaluated_outcomes_are_not_stale(self) -> None:
        self.tracker.record_detection("tech_a", "AAPL", {"price": 100, "atr": 2}, now=FRIDAY)
        self.tracker.evaluate_due(lambda s, a, b: _bars((103, 99, 102)), now=AFTER)

        self.assertEqual(self.repository.stale_pending_count("2026-08-01T00:00:00+00:00"), 0)

    def test_stale_helper_and_event_alarm(self) -> None:
        # Seed a detection whose evaluation has been due for weeks so the
        # check is independent of the wall clock when the test runs.
        self.tracker.record_detection(
            "tech_a", "AAPL", {"price": 100}, now="2026-06-05T14:00:00+00:00"
        )
        recorded: list[tuple[Any, ...]] = []

        class FakeEventStore:
            def record(self, *args: Any, **kwargs: Any) -> None:
                recorded.append((args, kwargs))

        app = SimpleNamespace(
            state=SimpleNamespace(engine=SimpleNamespace(event_store=FakeEventStore()))
        )

        stale = _stale_pending_outcomes(self.tracker, 0)
        self.assertGreater(stale, 0)
        _record_stale_outcomes_event(app, stale)
        _record_stale_outcomes_event(app, stale)  # throttled: one alarm per day

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0][1], "detection_outcomes_stale")


if __name__ == "__main__":
    unittest.main()
