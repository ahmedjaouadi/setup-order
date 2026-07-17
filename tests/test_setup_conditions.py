from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.engine.setup_condition_tracker import SetupConditionTracker
from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction
from app.setups.setup_conditions import humanize_invalidation_reason
from app.storage.database import Database
from app.storage.repositories import TradingRepository


def pullback_setup(status: str = "WAITING_ACTIVATION") -> dict:
    return {
        "setup_id": "PB1",
        "symbol": "TEST",
        "setup_type": "pullback_continuation",
        "status": status,
        "status_reason": "",
        "config": {
            "setup_id": "PB1",
            "symbol": "TEST",
            "setup_type": "pullback_continuation",
            "pullback": {"entry_reference": 50.0, "zone_min": 49.0, "zone_max": 50.5},
            "entry": {"trigger_offset": 0.02},
        },
    }


def momentum_setup() -> dict:
    return {
        "setup_id": "MB1",
        "symbol": "TEST",
        "setup_type": "momentum_breakout",
        "status": "WAITING_BREAKOUT",
        "status_reason": "",
        "config": {
            "setup_id": "MB1",
            "symbol": "TEST",
            "setup_type": "momentum_breakout",
            "breakout": {"resistance": 50.0},
        },
    }


def runner_setup() -> dict:
    return {
        "setup_id": "RN1",
        "symbol": "TEST",
        "setup_type": "runner",
        "status": "MANAGING_POSITION",
        "status_reason": "",
        "config": {"setup_id": "RN1", "symbol": "TEST", "setup_type": "runner"},
    }


def snapshot(**overrides) -> MarketSnapshot:
    values = {
        "symbol": "TEST",
        "price": 51.0,
        "open": 50.5,
        "close": 51.0,
        "ema_20": 50.0,
        "ema_50": 48.0,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


def hold_signal(reason: str = "Waiting for pullback") -> SetupSignal:
    return SetupSignal.hold(reason)


class SetupConditionTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.tracker = SetupConditionTracker(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_sequential_evaluation_single_in_progress(self) -> None:
        signal = SetupSignal(
            action=SignalAction.STATUS_CHANGE,
            reason="Trend filter confirmed",
            target_status=SetupStatus.WAITING_ENTRY_SIGNAL,
        )
        payload = self.tracker.update_from_evaluation(
            pullback_setup(),
            SetupStatus.WAITING_ACTIVATION,
            signal,
            snapshot(),
        )

        statuses = [condition["status"] for condition in payload["conditions"]]
        self.assertEqual(statuses, ["validated", "in_progress", "pending"])
        self.assertEqual(payload["current_step"], 1)
        self.assertEqual(payload["overall_status"], "watching")
        self.assertIn("1/3 conditions validees", payload["summary_message"])
        self.assertIn("EMA20", payload["conditions"][1]["target"])
        self.assertEqual(statuses.count("in_progress"), 1)

    def test_validated_timestamp_is_persisted_not_recomputed(self) -> None:
        signal = SetupSignal(
            action=SignalAction.STATUS_CHANGE,
            reason="Trend filter confirmed",
            target_status=SetupStatus.WAITING_ENTRY_SIGNAL,
        )
        with patch(
            "app.engine.setup_condition_tracker.utc_now_iso",
            return_value="2026-07-16T10:00:00+00:00",
        ):
            first = self.tracker.update_from_evaluation(
                pullback_setup(),
                SetupStatus.WAITING_ACTIVATION,
                signal,
                snapshot(),
            )
        self.assertEqual(first["conditions"][0]["validated_at"], "2026-07-16T10:00:00+00:00")

        fresh_tracker = SetupConditionTracker(self.repository)
        with patch(
            "app.engine.setup_condition_tracker.utc_now_iso",
            return_value="2026-07-16T10:05:00+00:00",
        ):
            second = fresh_tracker.update_from_evaluation(
                pullback_setup("WAITING_ENTRY_SIGNAL"),
                SetupStatus.WAITING_ENTRY_SIGNAL,
                hold_signal(),
                snapshot(),
            )
        self.assertEqual(second["conditions"][0]["validated_at"], "2026-07-16T10:00:00+00:00")

    def test_entry_ready_validates_everything(self) -> None:
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Pullback continuation confirmed",
            target_status=SetupStatus.ENTRY_READY,
            entry_price=50.2,
        )
        payload = self.tracker.update_from_evaluation(
            pullback_setup("WAITING_ENTRY_SIGNAL"),
            SetupStatus.WAITING_ENTRY_SIGNAL,
            signal,
            snapshot(price=49.8, close=49.8, open=49.4),
        )

        self.assertEqual(payload["overall_status"], "ready_to_enter")
        self.assertTrue(
            all(condition["status"] == "validated" for condition in payload["conditions"])
        )
        self.assertIsNone(payload["current_step"])
        self.assertIn("Toutes les conditions sont reunies", payload["summary_message"])

    def test_invalidation_marks_failed_condition_and_reason(self) -> None:
        signal = SetupSignal(
            action=SignalAction.INVALIDATE,
            reason="Price lost EMA 50 trend filter",
            target_status=SetupStatus.INVALIDATED,
        )
        payload = self.tracker.update_from_evaluation(
            pullback_setup("WAITING_ENTRY_SIGNAL"),
            SetupStatus.WAITING_ENTRY_SIGNAL,
            signal,
            snapshot(price=47.0, close=47.0),
        )

        self.assertEqual(payload["overall_status"], "invalidated")
        # La raison est traduite pour l'affichage; le marquage de la condition
        # en echec continue de se faire sur la raison BRUTE.
        self.assertEqual(
            payload["invalidation_reason"],
            "Le prix a perdu le filtre de tendance EMA 50",
        )
        by_id = {condition["id"]: condition for condition in payload["conditions"]}
        self.assertEqual(by_id["uptrend"]["status"], "failed")
        self.assertIn("Setup invalide", payload["summary_message"])

    def test_lifecycle_invalidation_codes_are_translated_for_display(self) -> None:
        """Les codes internes du lifecycle ne doivent jamais atteindre le bandeau."""
        expected = {
            "INVALIDATION_LEVEL_BROKEN": "Le niveau d'invalidation du setup a ete casse",
            "SUPPORT_BROKEN": "Le support du setup a ete casse",
            "TECHNICAL_THESIS_BROKEN": "La these technique du setup n'est plus valide",
            "STOP_ABOVE_ENTRY_FOR_LONG": "Stop place au-dessus de l'entree sur un setup long",
            "STOP_BELOW_ENTRY_FOR_SHORT": "Stop place en dessous de l'entree sur un setup short",
        }
        for code, message in expected.items():
            with self.subTest(code=code):
                setup = pullback_setup("INVALIDATED")
                setup["status_reason"] = code
                payload = self.tracker.conditions_payload(setup)

                self.assertEqual(payload["invalidation_reason"], message)
                self.assertNotIn(code, payload["summary_message"])

    def test_unknown_invalidation_reason_falls_back_to_raw_value(self) -> None:
        """Fallback: une raison inconnue reste affichee, jamais masquee."""
        signal = SetupSignal(
            action=SignalAction.INVALIDATE,
            reason="UNKNOWN_FUTURE_CODE_42",
            target_status=SetupStatus.INVALIDATED,
        )
        payload = self.tracker.update_from_evaluation(
            pullback_setup("WAITING_ENTRY_SIGNAL"),
            SetupStatus.WAITING_ENTRY_SIGNAL,
            signal,
            snapshot(price=47.0, close=47.0),
        )

        self.assertEqual(payload["invalidation_reason"], "UNKNOWN_FUTURE_CODE_42")
        self.assertIn("UNKNOWN_FUTURE_CODE_42", payload["summary_message"])

    def test_humanize_invalidation_reason_is_idempotent(self) -> None:
        """conditions_payload peut retraduire une raison deja persistee traduite."""
        once = humanize_invalidation_reason("SUPPORT_BROKEN")
        self.assertEqual(humanize_invalidation_reason(once), once)
        self.assertEqual(humanize_invalidation_reason(""), "")

    def test_momentum_conditions_read_engine_analysis(self) -> None:
        signal = hold_signal("SPREAD_TOO_WIDE")
        signal.metadata = {
            "analysis": {
                "decision_status": "PAUSED_VOLATILITY_OR_SPREAD_TOO_HIGH",
                "market": {"bid": 49.9, "ask": 50.1},
                "spread_check": {"ok": False, "spread_bps": 55.0, "max_spread_bps": 30},
            }
        }
        payload = self.tracker.update_from_evaluation(
            momentum_setup(),
            SetupStatus.WAITING_BREAKOUT,
            signal,
            snapshot(),
        )

        statuses = [condition["status"] for condition in payload["conditions"]]
        self.assertEqual(statuses[0], "validated")
        self.assertEqual(statuses[1], "in_progress")
        self.assertTrue(all(status == "pending" for status in statuses[2:]))
        self.assertIn("55.0 bps", payload["conditions"][1]["observed_value"])

    def test_rearm_resets_sequence(self) -> None:
        invalidate = SetupSignal(
            action=SignalAction.INVALIDATE,
            reason="Price lost EMA 50 trend filter",
            target_status=SetupStatus.INVALIDATED,
        )
        self.tracker.update_from_evaluation(
            pullback_setup("WAITING_ENTRY_SIGNAL"),
            SetupStatus.WAITING_ENTRY_SIGNAL,
            invalidate,
            snapshot(price=47.0, close=47.0),
        )
        payload = self.tracker.update_from_evaluation(
            pullback_setup(),
            SetupStatus.WAITING_ACTIVATION,
            hold_signal("Waiting for EMA data"),
            snapshot(ema_20=None, ema_50=None),
        )

        self.assertEqual(payload["overall_status"], "watching")
        self.assertEqual(payload["conditions"][0]["status"], "in_progress")
        self.assertIsNone(payload["conditions"][0]["validated_at"])

    def test_conditions_payload_without_history_builds_initial_checklist(self) -> None:
        payload = self.tracker.conditions_payload(pullback_setup())

        self.assertEqual(payload["setup_name"], "Pullback Continuation")
        self.assertEqual(payload["setup_direction"], "long")
        self.assertFalse(payload["management_only"])
        self.assertEqual(len(payload["conditions"]), 3)
        self.assertEqual(payload["current_step"], 0)
        self.assertEqual(payload["overall_status"], "watching")

    def test_ready_then_blocked_entry_keeps_history(self) -> None:
        # ENTRY_READY emis mais ordre retenu (broker/garde-fous): le statut
        # reste WAITING_ENTRY_SIGNAL; ni la lecture ni le tick suivant ne
        # doivent reinitialiser la sequence ni perdre les timestamps.
        ready = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Pullback continuation confirmed",
            target_status=SetupStatus.ENTRY_READY,
        )
        with patch(
            "app.engine.setup_condition_tracker.utc_now_iso",
            return_value="2026-07-16T10:00:00+00:00",
        ):
            self.tracker.update_from_evaluation(
                pullback_setup("WAITING_ENTRY_SIGNAL"),
                SetupStatus.WAITING_ENTRY_SIGNAL,
                ready,
                snapshot(price=49.8, close=49.8, open=49.4),
            )

        read_back = self.tracker.conditions_payload(pullback_setup("WAITING_ENTRY_SIGNAL"))
        self.assertEqual(read_back["overall_status"], "ready_to_enter")
        self.assertEqual(
            read_back["conditions"][0]["validated_at"], "2026-07-16T10:00:00+00:00"
        )

        followup = self.tracker.update_from_evaluation(
            pullback_setup("WAITING_ENTRY_SIGNAL"),
            SetupStatus.WAITING_ENTRY_SIGNAL,
            hold_signal(),
            snapshot(),
        )
        self.assertEqual(
            followup["conditions"][0]["validated_at"], "2026-07-16T10:00:00+00:00"
        )

    def test_conditions_payload_reconciles_with_setup_status(self) -> None:
        self.tracker.update_from_evaluation(
            pullback_setup(),
            SetupStatus.WAITING_ACTIVATION,
            hold_signal(),
            snapshot(),
        )
        payload = self.tracker.conditions_payload(pullback_setup("IN_POSITION"))

        self.assertEqual(payload["overall_status"], "entered")
        self.assertIn("Position prise", payload["summary_message"])

    def test_management_only_payload_has_no_checklist(self) -> None:
        payload = self.tracker.conditions_payload(runner_setup())

        self.assertTrue(payload["management_only"])
        self.assertEqual(payload["conditions"], [])
        self.assertEqual(payload["overall_status"], "entered")
        self.assertIn("gestion de position", payload["summary_message"])

    def test_delete_setup_removes_condition_state(self) -> None:
        self.tracker.update_from_evaluation(
            pullback_setup(),
            SetupStatus.WAITING_ACTIVATION,
            hold_signal(),
            snapshot(),
        )
        self.assertIsNotNone(self.repository.get_setup_condition_state("PB1"))

        self.repository.delete_setup("PB1")

        self.assertIsNone(self.repository.get_setup_condition_state("PB1"))


if __name__ == "__main__":
    unittest.main()
