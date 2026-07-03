from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.engine.opportunity_alert_service import (
    OpportunityAlertService,
    opportunity_event_type,
    score_processed_item,
)
from app.models import MarketSnapshot, SignalAction
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class OpportunityAlertServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.monotonic_now = 100.0
        self.service = OpportunityAlertService(
            self.repository,
            self.event_store,
            near_ready_threshold=0.96,
            cooldown_seconds=300,
            monotonic_provider=lambda: self.monotonic_now,
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_score_uses_relevant_trace_checks_and_ignores_auto_gate(self) -> None:
        score = score_processed_item(
            processed_item(
                action=SignalAction.HOLD.value,
                checks=[
                    check("Suivi setup", "ok"),
                    check("Execution auto TWS", "wait", actual="OFF"),
                    check("Prix dans zone retest", "ok"),
                    check("Volume relatif", "ok"),
                    check("Bougie de confirmation", "ok"),
                    check("Spread", "info"),
                ],
            )
        )

        self.assertEqual(score["label"], "NEAR_READY")
        self.assertEqual(score["percent"], 96.2)
        self.assertFalse(score["auto_execution_enabled"])
        self.assertEqual(score["total_checks"], 4)
        self.assertEqual(score["waiting_checks"], [])
        self.assertEqual(opportunity_event_type(score), "opportunity_near_ready")

    def test_entry_ready_is_scored_as_ready_even_when_auto_is_off(self) -> None:
        score = score_processed_item(
            processed_item(
                action=SignalAction.ENTRY_READY.value,
                checks=[
                    check("Execution auto TWS", "wait", actual="OFF"),
                    check("Signal entree", "ok"),
                ],
            )
        )

        self.assertEqual(score["label"], "READY")
        self.assertEqual(score["percent"], 100.0)
        self.assertFalse(score["auto_execution_enabled"])
        self.assertEqual(opportunity_event_type(score), "opportunity_ready")

    def test_near_ready_alert_is_deduplicated_during_cooldown(self) -> None:
        processed = [
            processed_item(
                action=SignalAction.HOLD.value,
                checks=[
                    check("Execution auto TWS", "ok", actual="ON"),
                    check("Prix dans zone retest", "ok"),
                    check("Volume relatif", "ok"),
                    check("Bougie de confirmation", "ok"),
                    check("Spread", "info"),
                ],
            )
        ]
        self.service.enrich_processed_items(processed)

        self.service.record_alerts(snapshot(), processed)
        self.service.record_alerts(snapshot(), processed)

        events = self.repository.list_events(event_type="opportunity_near_ready", limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["score"]["label"], "NEAR_READY")

    def test_ready_watch_alert_records_auto_off_without_order_permission(self) -> None:
        processed = [
            processed_item(
                action=SignalAction.ENTRY_READY.value,
                checks=[
                    check("Execution auto TWS", "wait", actual="OFF"),
                    check("Signal entree", "ok"),
                ],
            )
        ]
        self.service.enrich_processed_items(processed)

        self.service.record_alerts(snapshot(), processed)

        events = self.repository.list_events(event_type="opportunity_ready", limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["level"], "WARNING")
        self.assertIn("READY WATCH", events[0]["message"])
        self.assertFalse(events[0]["data"]["score"]["auto_execution_enabled"])


def processed_item(
    action: str,
    checks: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "setup_id": "UEC_2026_001",
        "setup_type": "breakout_retest",
        "status": "WAITING_ENTRY_SIGNAL",
        "action": action,
        "reason": "Retest almost confirmed",
        "trace": {
            "phase": "Recherche signal entree",
            "next_step": "Attendre confirmation finale.",
            "checks": checks,
        },
    }


def check(
    label: str,
    state: str,
    actual: object = "ok",
    expected: object = "ok",
) -> dict[str, object]:
    return {
        "label": label,
        "state": state,
        "actual": actual,
        "expected": expected,
    }


def snapshot() -> MarketSnapshot:
    return MarketSnapshot(symbol="UEC", price=14.35)


if __name__ == "__main__":
    unittest.main()
