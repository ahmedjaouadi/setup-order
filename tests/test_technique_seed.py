from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.opportunity_scanner.detectors import detect_opportunity_types
from app.opportunity_scanner.technique_evaluator import TechniqueEvaluator
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.opportunity_scanner.technique_seed import (
    apply_builtin_spread_filter_migration,
    builtin_technique_definitions,
    f1_technique_definitions,
    seed_builtin_techniques,
)
from app.storage.database import Database
from app.storage.repositories import TradingRepository

SEEDED_TECHNIQUE_COUNT = 9  # 7 legacy builtins + 2 F1 techniques (TODO 7.6)

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

    def test_seed_inserts_all_builtins(self) -> None:
        seed_builtin_techniques(self.repository)
        rows = self.repository.list_all()
        self.assertEqual(len(rows), SEEDED_TECHNIQUE_COUNT)
        self.assertTrue(all(row["origin"] == "builtin" for row in rows))
        self.assertTrue(all(row["status"] == "ACTIVE" for row in rows))
        self.assertTrue(all(row["enabled"] == 1 for row in rows))

    def test_seed_is_idempotent(self) -> None:
        seed_builtin_techniques(self.repository)
        seed_builtin_techniques(self.repository)
        seed_builtin_techniques(self.repository)
        rows = self.repository.list_all()
        self.assertEqual(len(rows), SEEDED_TECHNIQUE_COUNT)
        ids = [row["technique_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_definitions_have_unique_ids(self) -> None:
        definitions = (*builtin_technique_definitions(), *f1_technique_definitions())
        ids = [item["technique_id"] for item in definitions]
        self.assertEqual(len(ids), len(set(ids)))

    def test_f1_descriptions_cite_their_skills_section(self) -> None:
        # Mapping section 6.5: every new technique must cite its skills.md section.
        for definition in f1_technique_definitions():
            self.assertIn("skills.md", definition["description"], definition["technique_id"])


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


class F1TechniqueDetectionTests(unittest.TestCase):
    """GAP_AND_GO_FULL and MOMENTUM_RVOL_CONFIRMED on synthetic snapshots (TODO 7.6)."""

    GAP_AND_GO_SNAPSHOT = {
        "gap_pct": 3.5,
        "perf_stock_1d": 2.0,
        "dist_vwap_pct": 0.4,
        "rvol": 1.8,
        "spread_pct": 0.2,
        "time_bucket": "MORNING",
    }
    MOMENTUM_SNAPSHOT = {
        "perf_stock_1d": 6.0,
        "rs_spy": 1.2,
        "rvol": 1.8,
        "spread_pct": 0.2,
        "time_bucket": "MORNING",
    }

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

    def _types(self, snapshot: dict[str, Any]) -> list[str]:
        types, _detected_by = self.evaluator.evaluate(self.techniques, snapshot)
        return types

    def test_gap_and_go_full_detects(self) -> None:
        self.assertIn("GAP_AND_GO_FULL", self._types(self.GAP_AND_GO_SNAPSHOT))

    def test_gap_and_go_full_requires_holding_above_vwap(self) -> None:
        below_vwap = {**self.GAP_AND_GO_SNAPSHOT, "dist_vwap_pct": -0.2}
        self.assertNotIn("GAP_AND_GO_FULL", self._types(below_vwap))
        missing_vwap = dict(self.GAP_AND_GO_SNAPSHOT)
        del missing_vwap["dist_vwap_pct"]
        self.assertNotIn("GAP_AND_GO_FULL", self._types(missing_vwap))

    def test_gap_and_go_full_requires_tradeable_spread(self) -> None:
        wide = {**self.GAP_AND_GO_SNAPSHOT, "spread_pct": 0.8}
        self.assertNotIn("GAP_AND_GO_FULL", self._types(wide))

    def test_momentum_rvol_confirmed_detects(self) -> None:
        self.assertIn("MOMENTUM_RVOL_CONFIRMED", self._types(self.MOMENTUM_SNAPSHOT))

    def test_momentum_rvol_confirmed_requires_rvol(self) -> None:
        weak_volume = {**self.MOMENTUM_SNAPSHOT, "rvol": 1.2}
        self.assertNotIn("MOMENTUM_RVOL_CONFIRMED", self._types(weak_volume))

    def test_lunch_bucket_still_matches_when_rvol_confirms(self) -> None:
        # Lunch penalty (skills.md 25bis): during LUNCH the any-clause falls back
        # to the rvol leg, which the seeded thresholds satisfy here.
        lunch = {**self.MOMENTUM_SNAPSHOT, "time_bucket": "LUNCH"}
        self.assertIn("MOMENTUM_RVOL_CONFIRMED", self._types(lunch))


class SpreadFilterMigrationTests(unittest.TestCase):
    """One-shot migration of the 7 legacy builtins (TODO 7.7)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TechniqueRepository(self.database)
        self.state_store = TradingRepository(self.database)
        self.traces: list[dict[str, Any]] = []
        seed_builtin_techniques(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _event_store(self) -> Any:
        traces = self.traces

        class _Events:
            def record_decision_trace(self, **kwargs: Any) -> str:
                traces.append(kwargs)
                return "trace"

        return _Events()

    def test_migration_adds_spread_filter_and_bumps_revision(self) -> None:
        result = apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        self.assertTrue(result["applied"])
        legacy_ids = [item["technique_id"] for item in builtin_technique_definitions()]
        self.assertEqual(result["migrated"], legacy_ids)
        for technique_id in legacy_ids:
            row = self.repository.get(technique_id)
            assert row is not None
            self.assertIn("spread_pct", str(row["rule_json"]), technique_id)
            self.assertEqual(int(row["revision"]), 2, technique_id)
        # F1 techniques already carry the filter: untouched, still revision 1.
        for definition in f1_technique_definitions():
            row = self.repository.get(definition["technique_id"])
            assert row is not None
            self.assertEqual(int(row["revision"]), 1)

    def test_migration_traces_every_rewrite_with_before_after(self) -> None:
        apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        self.assertEqual(len(self.traces), len(builtin_technique_definitions()))
        for trace in self.traces:
            self.assertEqual(trace["decision_type"], "TECHNIQUE_REVISION")
            payload = trace["trace"]
            self.assertIn("rule_before", payload)
            self.assertIn("rule_after", payload)
            self.assertEqual(payload["revision_from"], 1)
            self.assertEqual(payload["revision_to"], 2)

    def test_migration_is_one_shot(self) -> None:
        apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        second = apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        self.assertFalse(second["applied"])
        self.assertEqual(second["reason"], "already_applied")
        row = self.repository.get("intraday_momentum_anomaly_v1")
        assert row is not None
        self.assertEqual(int(row["revision"]), 2)

    def test_migration_runs_even_with_learning_kill_switch_off(self) -> None:
        # Invariant 4: the migration is NOT learning - it applies regardless of
        # learning.enabled, but stays one-shot and fully traced.
        result = apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        self.assertTrue(result["applied"])
        self.assertTrue(self.traces)

    def test_wide_spread_matches_nothing_after_migration(self) -> None:
        apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        techniques = self.repository.list_active()
        evaluator = TechniqueEvaluator()
        rich_snapshot = {
            "perf_stock_1d": 12,
            "rs_spy": 5,
            "rs_sector": 3,
            "relative_volume": 2.0,
            "breakout_proximity": 1.0,
            "gap_pct": 4,
            "new_intraday_high": True,
            "spread_pct": 0.8,
        }
        types, _ = evaluator.evaluate(techniques, rich_snapshot)
        self.assertEqual(types, [])

    def test_missing_spread_matches_nothing_after_migration(self) -> None:
        # No spread data = no detection, consistent with the data-quality gate.
        apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        techniques = self.repository.list_active()
        evaluator = TechniqueEvaluator()
        types, _ = evaluator.evaluate(techniques, {"perf_stock_1d": 12, "gap_pct": 4})
        self.assertEqual(types, [])

    def test_acceptable_spread_keeps_legacy_behaviour(self) -> None:
        # Restricted to the 7 legacy builtins: the F1 techniques are new
        # detections by design, not part of the legacy contract.
        apply_builtin_spread_filter_migration(
            self.repository, self.state_store, self._event_store()
        )
        legacy_ids = {item["technique_id"] for item in builtin_technique_definitions()}
        techniques = [
            row for row in self.repository.list_active() if row["technique_id"] in legacy_ids
        ]
        evaluator = TechniqueEvaluator()
        for snapshot in NON_REGRESSION_SNAPSHOTS:
            with self.subTest(snapshot=snapshot):
                expected = detect_opportunity_types(snapshot)
                actual, _ = evaluator.evaluate(techniques, {**snapshot, "spread_pct": 0.2})
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
