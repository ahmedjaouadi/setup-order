from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.engine.position_action_executor import PositionActionExecutor
from app.engine.position_manager import PositionManager
from app.engine.state_machine import StateMachine
from app.models import SignalAction, SetupSignal, SetupStatus
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class PositionActionExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.position_manager = PositionManager(self.repository, self.event_store)
        self.executor = PositionActionExecutor(
            repository=self.repository,
            event_store=self.event_store,
            position_manager=self.position_manager,
            state_machine=StateMachine(),
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _open_setup_position(self) -> tuple[str, str]:
        setup = BreakoutRetestSetup(valid_breakout_config()).to_record(
            SetupStatus.IN_POSITION
        )
        self.repository.upsert_setup(setup)
        self.position_manager.open_or_update_position(
            setup_id=setup.setup_id,
            symbol=setup.symbol,
            quantity=10,
            average_price=14.50,
            current_price=15.00,
            stop_loss=13.85,
        )
        return setup.setup_id, setup.symbol

    def test_raise_stop_signal_updates_position_and_transitions_setup(self) -> None:
        setup_id, symbol = self._open_setup_position()
        setup_row = self.repository.get_setup(setup_id)
        signal = SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason="Trailing stop rule reached",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=14.25,
        )

        handled = self.executor.execute_raise_stop_signal(
            setup_row,
            SetupStatus.IN_POSITION,
            signal,
        )

        updated_setup = self.repository.get_setup(setup_id)
        updated_position = self.repository.get_position(symbol)
        self.assertTrue(handled)
        self.assertEqual(updated_setup["status"], SetupStatus.MANAGING_POSITION.value)
        self.assertEqual(updated_position["current_stop"], 14.25)

    def test_lower_stop_signal_is_rejected_without_transition(self) -> None:
        setup_id, symbol = self._open_setup_position()
        setup_row = self.repository.get_setup(setup_id)
        signal = SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason="Bad stop rule",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=13.50,
        )

        handled = self.executor.execute_raise_stop_signal(
            setup_row,
            SetupStatus.IN_POSITION,
            signal,
        )

        updated_setup = self.repository.get_setup(setup_id)
        updated_position = self.repository.get_position(symbol)
        events = self.repository.list_events(setup_id=setup_id, limit=5)
        self.assertTrue(handled)
        self.assertEqual(updated_setup["status"], SetupStatus.IN_POSITION.value)
        self.assertEqual(updated_position["current_stop"], 13.85)
        self.assertTrue(
            any(event["event_type"] == "stop_move_rejected" for event in events)
        )

    def test_manual_move_stop_uses_same_position_manager_rules(self) -> None:
        _, symbol = self._open_setup_position()

        moved = self.executor.move_stop(symbol, 14.10)

        updated_position = self.repository.get_position(symbol)
        self.assertTrue(moved)
        self.assertEqual(updated_position["current_stop"], 14.10)


if __name__ == "__main__":
    unittest.main()
