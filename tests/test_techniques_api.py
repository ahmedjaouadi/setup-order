from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.api import routes_techniques
from app.opportunity_scanner.learning_loop import LearningLoop
from app.opportunity_scanner.outcome_repository import OutcomeRepository
from app.opportunity_scanner.schemas import (
    OutcomeFeedbackRequest,
    TechniqueCreateRequest,
    TechniquePatchRequest,
)
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.opportunity_scanner.technique_seed import seed_builtin_techniques
from app.opportunity_scanner.technique_service import TechniqueService
from app.storage.database import Database

BUILTIN_ID = "intraday_momentum_anomaly_v1"


class TechniquesApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TechniqueRepository(self.database)
        seed_builtin_techniques(self.repository)
        self.outcomes = OutcomeRepository(self.database)
        self.service = TechniqueService(
            self.repository,
            outcomes_provider=self.outcomes.outcomes_for_technique,
            feedback_recorder=self.outcomes.set_feedback,
        )
        self.learning_loop = LearningLoop(self.repository, lambda: {}, settings={})
        self.request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    techniques=self.service,
                    learning_loop=self.learning_loop,
                )
            )
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_list_returns_seeded_builtins(self) -> None:
        result = await routes_techniques.list_techniques(self.request)
        items = result["items"]
        self.assertEqual(len(items), 7)
        self.assertTrue(all(item["origin"] == "builtin" for item in items))
        # Response carries a human-readable rule summary for the UI.
        self.assertTrue(all(item["rule_summary"] for item in items))

    async def test_create_manual_technique(self) -> None:
        payload = TechniqueCreateRequest(
            name="High gap",
            opportunity_type="GAP_AND_HOLD",
            rule={"field": "gap_pct", "op": ">=", "value": 5},
        )
        created = await routes_techniques.create_technique(self.request, payload)
        self.assertEqual(created["origin"], "manual")
        self.assertEqual(created["status"], "ACTIVE")
        self.assertEqual(created["opportunity_type"], "GAP_AND_HOLD")
        self.assertEqual(created["rule"]["opportunity_type"], "GAP_AND_HOLD")
        listed = await routes_techniques.list_techniques(self.request)
        self.assertEqual(len(listed["items"]), 8)

    async def test_create_with_field_outside_whitelist_returns_400(self) -> None:
        payload = TechniqueCreateRequest(
            name="Bad rule",
            opportunity_type="CUSTOM",
            rule={"field": "not_whitelisted", "op": ">=", "value": 1},
        )
        with self.assertRaises(HTTPException) as ctx:
            await routes_techniques.create_technique(self.request, payload)
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_get_unknown_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await routes_techniques.get_technique(self.request, "does_not_exist")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_patch_toggles_enabled(self) -> None:
        patched = await routes_techniques.patch_technique(
            self.request, BUILTIN_ID, TechniquePatchRequest(enabled=False)
        )
        self.assertFalse(patched["enabled"])
        reenabled = await routes_techniques.patch_technique(
            self.request, BUILTIN_ID, TechniquePatchRequest(enabled=True)
        )
        self.assertTrue(reenabled["enabled"])

    async def test_patch_unknown_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await routes_techniques.patch_technique(
                self.request, "nope", TechniquePatchRequest(enabled=False)
            )
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_patch_invalid_rule_returns_400(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await routes_techniques.patch_technique(
                self.request,
                BUILTIN_ID,
                TechniquePatchRequest(rule={"field": "bogus", "op": ">=", "value": 1}),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_delete_manual_soft_deletes_to_retired(self) -> None:
        created = await routes_techniques.create_technique(
            self.request,
            TechniqueCreateRequest(
                name="Temp",
                opportunity_type="TEMP",
                rule={"field": "gap_pct", "op": ">=", "value": 5},
            ),
        )
        technique_id = created["technique_id"]
        deleted = await routes_techniques.delete_technique(self.request, technique_id)
        self.assertEqual(deleted["status"], "RETIRED")
        self.assertFalse(deleted["enabled"])
        # Soft delete: the row still exists and is retrievable.
        still_there = await routes_techniques.get_technique(self.request, technique_id)
        self.assertEqual(still_there["status"], "RETIRED")

    async def test_delete_builtin_only_disables_never_retires(self) -> None:
        deleted = await routes_techniques.delete_technique(self.request, BUILTIN_ID)
        self.assertEqual(deleted["status"], "ACTIVE")
        self.assertFalse(deleted["enabled"])

    async def test_delete_unknown_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await routes_techniques.delete_technique(self.request, "nope")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_outcomes_empty_for_known_and_404_for_unknown(self) -> None:
        result = await routes_techniques.technique_outcomes(self.request, BUILTIN_ID)
        self.assertEqual(result["items"], [])
        with self.assertRaises(HTTPException) as ctx:
            await routes_techniques.technique_outcomes(self.request, "nope")
        self.assertEqual(ctx.exception.status_code, 404)

    def _seed_outcome(self, outcome_id: str) -> None:
        self.outcomes.create_outcome(
            {
                "outcome_id": outcome_id,
                "technique_id": BUILTIN_ID,
                "symbol": "AAPL",
                "detected_at": "2026-07-03T14:00:00+00:00",
                "horizon": "1d",
                "evaluation_due_at": "2026-07-06T20:00:00+00:00",
                "status": "PENDING",
                "created_at": "2026-07-03T14:00:00+00:00",
            }
        )

    async def test_feedback_persists_canonical_value(self) -> None:
        self._seed_outcome("dco_1")
        result = await routes_techniques.set_outcome_feedback(
            self.request, "dco_1", OutcomeFeedbackRequest(feedback="too_late")
        )
        self.assertEqual(result["human_feedback"], "too_late")
        stored = self.outcomes.outcomes_for_technique(BUILTIN_ID)[0]
        self.assertEqual(stored["human_feedback"], "too_late")

    async def test_feedback_accepts_free_text(self) -> None:
        self._seed_outcome("dco_2")
        result = await routes_techniques.set_outcome_feedback(
            self.request, "dco_2", OutcomeFeedbackRequest(feedback="entered too high on the gap")
        )
        self.assertEqual(result["human_feedback"], "entered too high on the gap")

    async def test_feedback_unknown_outcome_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await routes_techniques.set_outcome_feedback(
                self.request, "missing", OutcomeFeedbackRequest(feedback="good")
            )
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_learning_run_respects_kill_switch_by_default(self) -> None:
        # Kill-switch defaults to off, so a forced run mutates nothing.
        result = await routes_techniques.run_learning(self.request)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["decisions"], [])

    async def test_stats_are_empty_before_outcome_tracking(self) -> None:
        result = await routes_techniques.list_techniques(self.request)
        stats = result["items"][0]["stats"]
        self.assertEqual(stats["sample_size"], 0)
        self.assertIsNone(stats["hit_rate"])
        self.assertEqual(stats["status_label"], "—")


if __name__ == "__main__":
    unittest.main()
