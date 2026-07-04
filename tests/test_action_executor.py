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


if __name__ == "__main__":
    unittest.main()
