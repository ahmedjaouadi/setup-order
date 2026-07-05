from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.opportunity_scanner.learning_loop import LearningLoop, scale_rule
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.storage.database import Database

RTH_NOW = datetime(2026, 7, 8, 14, 0, tzinfo=UTC)  # Wednesday 10:00 ET
OUTSIDE_RTH = datetime(2026, 7, 4, 14, 0, tzinfo=UTC)  # Saturday
ENABLED = {"opportunity_scanner": {"learning": {"enabled": True, "min_samples": 30}}}


class FakeEventStore:
    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def record_decision_trace(self, **kwargs: Any) -> str:
        self.traces.append(kwargs)
        return "trace_1"


def _insert(
    repo: TechniqueRepository,
    technique_id: str,
    *,
    origin: str = "manual",
    status: str = "ACTIVE",
    parent_id: str | None = None,
    value: float = 5,
) -> None:
    repo.insert_if_absent(
        {
            "technique_id": technique_id,
            "name": technique_id,
            "description": "",
            "rule_json": json.dumps(
                {"field": "gap_pct", "op": ">=", "value": value, "opportunity_type": technique_id}
            ),
            "enabled": True,
            "origin": origin,
            "parent_id": parent_id,
            "status": status,
            "created_at": "2026-07-01T00:00:00+00:00",
            "updated_at": "2026-07-01T00:00:00+00:00",
        }
    )


class ScaleRuleTests(unittest.TestCase):
    def test_scales_numeric_thresholds(self) -> None:
        rule = {"field": "gap_pct", "op": ">=", "value": 5, "opportunity_type": "X"}
        self.assertEqual(scale_rule(rule, 1.2)["value"], 6.0)
        self.assertEqual(scale_rule(rule, 0.8)["value"], 4.0)
        # opportunity_type and booleans are untouched.
        self.assertEqual(scale_rule(rule, 1.2)["opportunity_type"], "X")

    def test_scales_nested_and_between(self) -> None:
        rule = {"all": [{"field": "gap_pct", "op": "between", "value": [2, 4]}]}
        scaled = scale_rule(rule, 0.5)
        self.assertEqual(scaled["all"][0]["value"], [1.0, 2.0])


class LearningLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "s.sqlite")
        self.database.initialize()
        self.repo = TechniqueRepository(self.database)
        self.events = FakeEventStore()

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _loop(self, stats: dict[str, dict[str, Any]], settings: dict[str, Any]) -> LearningLoop:
        return LearningLoop(self.repo, lambda: stats, event_store=self.events, settings=settings)

    def test_kill_switch_off_makes_no_mutations(self) -> None:
        _insert(self.repo, "t1")
        loop = self._loop({"t1": {"sample_size": 50, "expectancy_r": -1.0}}, settings={})
        result = loop.run(now=RTH_NOW)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["decisions"], [])
        self.assertEqual(self.repo.get("t1")["status"], "ACTIVE")
        self.assertEqual(self.events.traces, [])

    def test_frozen_outside_rth(self) -> None:
        _insert(self.repo, "t1")
        loop = self._loop({"t1": {"sample_size": 50, "expectancy_r": -1.0}}, settings=ENABLED)
        result = loop.run(now=OUTSIDE_RTH)
        self.assertEqual(result.get("skipped"), "outside_rth")
        self.assertEqual(self.repo.get("t1")["status"], "ACTIVE")

    def test_warmup_makes_no_decision(self) -> None:
        _insert(self.repo, "t1")
        loop = self._loop({"t1": {"sample_size": 12, "expectancy_r": -5.0}}, settings=ENABLED)
        result = loop.run(now=RTH_NOW)
        self.assertEqual(result["decisions"], [])
        self.assertEqual(self.repo.get("t1")["status"], "ACTIVE")

    def test_negative_expectancy_retires_manual_technique(self) -> None:
        _insert(self.repo, "t1", origin="manual")
        loop = self._loop({"t1": {"sample_size": 40, "expectancy_r": -0.5}}, settings=ENABLED)
        result = loop.run(now=RTH_NOW)
        self.assertEqual(self.repo.get("t1")["status"], "RETIRED")
        self.assertEqual(result["decisions"][0]["action"], "RETIRED")
        self.assertTrue(self.events.traces)

    def test_builtin_is_disabled_not_retired(self) -> None:
        _insert(self.repo, "b1", origin="builtin")
        loop = self._loop({"b1": {"sample_size": 40, "expectancy_r": -0.5}}, settings=ENABLED)
        loop.run(now=RTH_NOW)
        row = self.repo.get("b1")
        self.assertEqual(row["status"], "ACTIVE")  # never RETIRED
        self.assertEqual(row["enabled"], 0)  # disabled only

    def test_positive_expectancy_spawns_candidates_once(self) -> None:
        _insert(self.repo, "t1", value=5)
        loop = self._loop({"t1": {"sample_size": 40, "expectancy_r": 1.5}}, settings=ENABLED)
        loop.run(now=RTH_NOW)
        candidates = [row for row in self.repo.list_all() if row["status"] == "CANDIDATE"]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(row["origin"] == "learned" for row in candidates))
        self.assertTrue(all(row["parent_id"] == "t1" for row in candidates))
        # Re-running does not spawn a second generation.
        loop.run(now=RTH_NOW)
        self.assertEqual(
            len([row for row in self.repo.list_all() if row["status"] == "CANDIDATE"]), 2
        )

    def test_candidate_promoted_when_better_than_parent(self) -> None:
        _insert(self.repo, "t1", value=5)
        _insert(
            self.repo, "t1_vup20", origin="learned", status="CANDIDATE", parent_id="t1", value=6
        )
        stats = {
            "t1": {"sample_size": 40, "expectancy_r": 1.0},
            "t1_vup20": {"sample_size": 40, "expectancy_r": 2.0},
        }
        loop = self._loop(stats, settings=ENABLED)
        loop.run(now=RTH_NOW)
        promoted = self.repo.get("t1_vup20")
        self.assertEqual(promoted["status"], "ACTIVE")
        self.assertEqual(promoted["origin"], "learned")

    def test_candidate_retired_when_not_better(self) -> None:
        _insert(self.repo, "t1", value=5)
        _insert(
            self.repo, "t1_vup20", origin="learned", status="CANDIDATE", parent_id="t1", value=6
        )
        stats = {
            "t1": {"sample_size": 40, "expectancy_r": 2.0},
            "t1_vup20": {"sample_size": 40, "expectancy_r": 1.0},
        }
        loop = self._loop(stats, settings=ENABLED)
        loop.run(now=RTH_NOW)
        self.assertEqual(self.repo.get("t1_vup20")["status"], "RETIRED")

    def test_active_cap_blocks_promotion(self) -> None:
        stats: dict[str, dict[str, Any]] = {}
        # 19 stable fillers (expectancy 0 -> no decision) + parent = 20 active, at the cap.
        for index in range(19):
            tid = f"a{index}"
            _insert(self.repo, tid)
            stats[tid] = {"sample_size": 40, "expectancy_r": 0.0}
        _insert(self.repo, "parent", value=5)
        stats["parent"] = {"sample_size": 40, "expectancy_r": 1.0}
        _insert(
            self.repo, "cand", origin="learned", status="CANDIDATE", parent_id="parent", value=6
        )
        stats["cand"] = {"sample_size": 40, "expectancy_r": 5.0}
        settings = {"opportunity_scanner": {"learning": {"enabled": True, "max_active": 20}}}
        loop = self._loop(stats, settings=settings)
        loop.run(now=RTH_NOW)
        # At the cap the promotable candidate is retired, never promoted past 20.
        self.assertEqual(self.repo.get("cand")["status"], "RETIRED")
        active = [r for r in self.repo.list_all() if r["status"] == "ACTIVE" and r["enabled"]]
        self.assertLessEqual(len(active), 20)


if __name__ == "__main__":
    unittest.main()
