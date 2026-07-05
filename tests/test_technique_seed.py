from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.opportunity_scanner.detectors import detect_opportunity_types
from app.opportunity_scanner.technique_evaluator import TechniqueEvaluator
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.opportunity_scanner.technique_seed import (
    builtin_technique_definitions,
    seed_builtin_techniques,
)
from app.storage.database import Database

# Synthetic snapshots covering each of the 7 builtin rules: match, non-match,
# boundary cases, and the field aliases `detectors.py` resolves today.
NON_REGRESSION_SNAPSHOTS: tuple[dict[str, Any], ...] = (
    {},
    {"perf_stock_1d": 5},
    {"perf_stock_1d": 4.99},
    {"stock_perf_1d": 5},  # alias
    {"rs_spy": 3},
    {"rs_spy": 2.99},
    {"rs_sector": 2},
    {"rs_sector": 1.99},
    {"relative_strength_vs_sector": 2},  # alias
    {"relative_strength_vs_spy": 3},  # alias
    {"relative_volume": 1.5},
    {"volume_ratio": 1.5},
    {"volume_ratio_15m": 1.5},
    {"relative_volume": 1.49},
    {"breakout_proximity": 1.5},
    {"breakout_proximity": 1.51},
    {"gap_pct": 3, "perf_stock_1d": 0.1},
    {"gap_pct": 3, "perf_stock_1d": 0},
    {"gap_pct": 2.9, "perf_stock_1d": 1},
    {"new_intraday_high": True},
    {"new_intraday_high": False},
    {"perf_stock_1d": 6, "gap_pct": 4, "rs_sector": 2.5},
    {
        "perf_stock_1d": 12,
        "rs_spy": 5,
        "rs_sector": 3,
        "relative_volume": 2.0,
        "breakout_proximity": 1.0,
        "gap_pct": 4,
        "new_intraday_high": True,
    },
)


class TechniqueSeedIdempotenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TechniqueRepository(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_seed_inserts_exactly_seven_builtins(self) -> None:
        seed_builtin_techniques(self.repository)
        rows = self.repository.list_all()
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row["origin"] == "builtin" for row in rows))
        self.assertTrue(all(row["status"] == "ACTIVE" for row in rows))
        self.assertTrue(all(row["enabled"] == 1 for row in rows))

    def test_seed_is_idempotent(self) -> None:
        seed_builtin_techniques(self.repository)
        seed_builtin_techniques(self.repository)
        seed_builtin_techniques(self.repository)
        rows = self.repository.list_all()
        self.assertEqual(len(rows), 7)
        ids = [row["technique_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_definitions_have_unique_ids(self) -> None:
        definitions = builtin_technique_definitions()
        ids = [item["technique_id"] for item in definitions]
        self.assertEqual(len(ids), len(set(ids)))


class NonRegressionTests(unittest.TestCase):
    """The technique library must reproduce detect_opportunity_types() exactly."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TechniqueRepository(self.database)
        seed_builtin_techniques(self.repository)
        self.techniques = self.repository.list_active()
        self.evaluator = TechniqueEvaluator()

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_library_matches_legacy_detectors_on_every_snapshot(self) -> None:
        for snapshot in NON_REGRESSION_SNAPSHOTS:
            with self.subTest(snapshot=snapshot):
                expected = detect_opportunity_types(snapshot)
                actual, _detected_by = self.evaluator.evaluate(self.techniques, snapshot)
                self.assertEqual(actual, expected)

    def test_detected_by_points_to_a_seeded_technique(self) -> None:
        snapshot = {"perf_stock_1d": 6, "gap_pct": 4, "rs_sector": 2.5}
        types, detected_by = self.evaluator.evaluate(self.techniques, snapshot)
        self.assertTrue(types)
        technique_ids = {row["technique_id"] for row in self.techniques}
        for opportunity_type in types:
            self.assertIn(opportunity_type, detected_by)
            self.assertIn(detected_by[opportunity_type], technique_ids)


if __name__ == "__main__":
    unittest.main()
