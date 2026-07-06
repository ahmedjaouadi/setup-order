from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.opportunity_scanner import MarketContextOpportunityScanner
from app.opportunity_scanner.learning_loop import LearningLoop
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.opportunity_scanner.technique_seed import seed_builtin_techniques
from app.storage.database import Database

# Every module introduced by the intelligent-scanner work. These must stay free
# of dynamic code execution and any coupling to the order manager.
NEW_MODULES = [
    "app/opportunity_scanner/rule_interpreter.py",
    "app/opportunity_scanner/technique_evaluator.py",
    "app/opportunity_scanner/technique_repository.py",
    "app/opportunity_scanner/technique_seed.py",
    "app/opportunity_scanner/technique_service.py",
    "app/opportunity_scanner/outcome_repository.py",
    "app/opportunity_scanner/outcome_tracker.py",
    "app/opportunity_scanner/learning_loop.py",
    "app/opportunity_scanner/context_tags.py",
    "app/opportunity_scanner/data_quality_gate.py",
    "app/opportunity_scanner/feature_math.py",
    "app/decision_codes.py",
    "app/api/routes_techniques.py",
]

RTH_NOW = datetime(2026, 7, 8, 14, 0, tzinfo=UTC)


class FakeEventStore:
    def __init__(self) -> None:
        self.traces: list[dict[str, object]] = []

    def record_decision_trace(self, **kwargs: object) -> str:
        self.traces.append(kwargs)
        return "trace_1"


class StaticInvariantTests(unittest.TestCase):
    def test_no_dynamic_code_execution(self) -> None:
        for path in NEW_MODULES:
            source = Path(path).read_text(encoding="utf-8")
            # re.compile is regex compilation, not code execution - not a risk.
            without_regex = source.replace("re.compile(", "")
            for forbidden in ("eval(", "exec(", "compile("):
                self.assertNotIn(forbidden, without_regex, f"{forbidden} found in {path}")

    def test_no_order_manager_coupling(self) -> None:
        for path in NEW_MODULES:
            source = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("order_manager", source, f"order_manager referenced in {path}")
            self.assertNotIn("engine.order", source, f"order engine referenced in {path}")


class ConsultativeInvariantTests(unittest.TestCase):
    def test_technique_signal_can_never_send_orders(self) -> None:
        scanner = MarketContextOpportunityScanner()
        signal = scanner.evaluate({"symbol": "AAPL", "perf_stock_1d": 9, "gap_pct": 4})
        self.assertFalse(signal["can_send_order"])


class LearningGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "s.sqlite")
        self.database.initialize()
        self.repo = TechniqueRepository(self.database)
        seed_builtin_techniques(self.repo)
        self.events = FakeEventStore()

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _snapshot(self) -> list[dict[str, object]]:
        return [
            {k: row[k] for k in ("technique_id", "status", "enabled")}
            for row in self.repo.list_all()
        ]

    def test_kill_switch_off_mutates_nothing(self) -> None:
        stats = {
            row["technique_id"]: {"sample_size": 100, "expectancy_r": -5.0}
            for row in self.repo.list_all()
        }
        before = self._snapshot()
        loop = LearningLoop(self.repo, lambda: stats, event_store=self.events, settings={})
        result = loop.run(now=RTH_NOW)
        self.assertFalse(result["enabled"])
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.events.traces, [])

    def test_stats_and_learning_frozen_outside_rth(self) -> None:
        stats = {
            row["technique_id"]: {"sample_size": 100, "expectancy_r": -5.0}
            for row in self.repo.list_all()
        }
        before = self._snapshot()
        settings = {"opportunity_scanner": {"learning": {"enabled": True}}}
        loop = LearningLoop(self.repo, lambda: stats, event_store=self.events, settings=settings)
        saturday = datetime(2026, 7, 4, 14, 0, tzinfo=UTC)
        result = loop.run(now=saturday)
        self.assertEqual(result.get("skipped"), "outside_rth")
        self.assertEqual(self._snapshot(), before)

    def test_every_automatic_decision_is_traced(self) -> None:
        # One builtin with negative expectancy -> exactly one disable decision + trace.
        target = self.repo.list_all()[0]["technique_id"]
        stats = {target: {"sample_size": 50, "expectancy_r": -1.0}}
        settings = {"opportunity_scanner": {"learning": {"enabled": True}}}
        loop = LearningLoop(self.repo, lambda: stats, event_store=self.events, settings=settings)
        result = loop.run(now=RTH_NOW)
        self.assertEqual(len(result["decisions"]), len(self.events.traces))
        self.assertTrue(self.events.traces)
        self.assertTrue(
            all(trace["decision_type"] == "TECHNIQUE_LEARNING" for trace in self.events.traces)
        )


if __name__ == "__main__":
    unittest.main()
