from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.engine.action_executor import ActionExecutor
from app.engine.state_machine import StateMachine
from app.models import SetupSignal, SetupStatus, SignalAction
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class ActionExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.executor = ActionExecutor(
            repository=self.repository,
            event_store=self.event_store,
            state_machine=StateMachine(),
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_status_change_updates_setup_and_records_event(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config()).to_record(
            SetupStatus.WAITING_ACTIVATION
        )
        self.repository.upsert_setup(setup)
        setup_row = self.repository.get_setup(setup.setup_id)
        signal = SetupSignal(
            action=SignalAction.STATUS_CHANGE,
            reason="Breakout confirmed",
            target_status=SetupStatus.WAITING_ENTRY_SIGNAL,
        )

        handled = self.executor.execute_simple_action(
            setup_row,
            SetupStatus.WAITING_ACTIVATION,
            signal,
        )

        updated = self.repository.get_setup(setup.setup_id)
        events = self.repository.list_events(setup_id=setup.setup_id, limit=5)
        self.assertTrue(handled)
        self.assertEqual(updated["status"], SetupStatus.WAITING_ENTRY_SIGNAL.value)
        self.assertTrue(any(event["event_type"] == "setup_status_changed" for event in events))

    def _make_setup_row(self, status: SetupStatus) -> dict:
        setup = BreakoutRetestSetup(valid_breakout_config()).to_record(status)
        self.repository.upsert_setup(setup)
        return self.repository.get_setup(setup.setup_id)

    def test_disallowed_transition_is_skipped_without_calling_state_machine(self) -> None:
        cases = [
            (SetupStatus.WAITING_RETEST, SetupStatus.MISSED_BREAKOUT),
            (SetupStatus.ENTRY_ORDER_PLACED, SetupStatus.INVALIDATED),
        ]
        for current_status, target_status in cases:
            with self.subTest(current=current_status, target=target_status):
                setup_row = self._make_setup_row(current_status)
                with self.assertLogs("app.engine.action_executor", level="DEBUG") as logs:
                    self.executor.transition_setup(
                        setup_row, current_status, target_status, "irrelevant"
                    )

                updated = self.repository.get_setup(setup_row["setup_id"])
                events = self.repository.list_events(setup_id=setup_row["setup_id"], limit=10)

                self.assertEqual(updated["status"], current_status.value)
                self.assertFalse(
                    any(event["event_type"] == "setup_transition_rejected" for event in events)
                )
                self.assertTrue(
                    any("transition_skipped_not_allowed" in line for line in logs.output)
                )

    def test_allowed_transitions_still_pass_and_write_status(self) -> None:
        cases = [
            (
                SetupStatus.MISSED_BREAKOUT,
                SetupStatus.WAITING_RETEST,
                SignalAction.STATUS_CHANGE,
            ),
            (
                SetupStatus.WAITING_ACTIVATION,
                SetupStatus.WAITING_ENTRY_SIGNAL,
                SignalAction.STATUS_CHANGE,
            ),
            (
                SetupStatus.WAITING_ENTRY_SIGNAL,
                SetupStatus.INVALIDATED,
                SignalAction.INVALIDATE,
            ),
            (
                SetupStatus.ENTRY_READY,
                SetupStatus.INVALIDATED,
                SignalAction.INVALIDATE,
            ),
        ]
        for current_status, target_status, action in cases:
            with self.subTest(current=current_status, target=target_status):
                setup_row = self._make_setup_row(current_status)
                signal = SetupSignal(
                    action=action,
                    reason="test",
                    target_status=target_status,
                )

                handled = self.executor.execute_simple_action(setup_row, current_status, signal)

                updated = self.repository.get_setup(setup_row["setup_id"])
                events = self.repository.list_events(setup_id=setup_row["setup_id"], limit=10)
                self.assertTrue(handled)
                self.assertEqual(updated["status"], target_status.value)
                self.assertTrue(
                    any(event["event_type"] == "setup_status_changed" for event in events)
                )

    def test_reconciling_existing_position_to_invalidated_is_not_short_circuited(self) -> None:
        current_status = SetupStatus.RECONCILING_EXISTING_POSITION
        target_status = SetupStatus.INVALIDATED
        setup_row = self._make_setup_row(current_status)
        signal = SetupSignal(
            action=SignalAction.INVALIDATE,
            reason="position no longer valid",
            target_status=target_status,
        )

        handled = self.executor.execute_simple_action(setup_row, current_status, signal)

        updated = self.repository.get_setup(setup_row["setup_id"])
        events = self.repository.list_events(setup_id=setup_row["setup_id"], limit=10)
        self.assertTrue(handled)
        self.assertEqual(updated["status"], target_status.value)
        self.assertTrue(any(event["event_type"] == "setup_status_changed" for event in events))


if __name__ == "__main__":
    unittest.main()
